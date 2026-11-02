"""Decimal arithmetic and timezone-aware, documented Toast reporting definitions."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
ZERO = Decimal('0')


def dec(value):
    return Decimal(str(value)) if value is not None else ZERO


def money(value):
    return float(dec(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def parsed(value):
    if not value:
        return None
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp requires a timezone')
    return result


def business_day(now, closeout=4):
    local = now.astimezone(ET)
    return local.date() - timedelta(days=int(local.hour < closeout))


def bounds(day, closeout=4):
    return (datetime.combine(day, time(closeout), ET), datetime.combine(day + timedelta(days=1), time(closeout), ET))


def reporting_week_start(day, start_weekday):
    """Return the first business date of the configured seven-day week."""
    return day - timedelta(days=(day.weekday() - start_weekday) % 7)


def percent(numerator, denominator):
    return money(dec(numerator) / dec(denominator) * 100) if dec(denominator) > 0 else None


def change(current, previous):
    return percent(dec(current) - dec(previous), previous)


def check_metrics(check):
    if check.get('voided') or check.get('deleted'):
        return ZERO, ZERO, ZERO
    if check.get('amount') is None:
        raise ValueError('Active check has no amount; sales cannot be calculated reliably.')
    total = dec(check['amount'])
    discount = sum((dec(d.get('discountAmount')) for d in check.get('appliedDiscounts', []) if not d.get('voided')), ZERO)
    for s in check.get('selections', []):
        if s.get('voided') or s.get('deleted'):
            continue
        if s.get('deferred') or s.get('selectionType') == 'HOUSE_ACCOUNT_PAY_BALANCE':
            total -= dec(s.get('price'))
        discount += sum((dec(d.get('discountAmount')) for d in s.get('appliedDiscounts', []) if not d.get('voided')), ZERO)
    for s in check.get('appliedServiceCharges', []):
        if s.get('serviceChargeCategory') == 'FUNDRAISING_CAMPAIGN':
            total -= dec(s.get('chargeAmount'))
    refund = sum((dec((p.get('refund') or {}).get('refundAmount')) for p in check.get('payments', [])), ZERO)
    return total - refund, discount, refund


def sales_summary(orders, day, now, closeout=4):
    total = discount = refunds = ZERO
    count = checks = voids = 0
    hourly = [ZERO] * 24
    upcoming = []
    seen = set()
    for order in orders:
        key = order.get('guid')
        if key and key in seen:
            continue
        seen.add(key)
        if order.get('deleted') or order.get('excessFood'):
            continue
        if order.get('voided'):
            voids += 1
            continue
        promised = parsed(order.get('promisedDate') or order.get('estimatedFulfillmentDate'))
        metrics = [check_metrics(c) for c in order.get('checks', [])]
        value = sum((m[0] for m in metrics), ZERO)
        if (promised and promised > now) or order.get('approvalStatus') == 'FUTURE':
            upcoming.append({'id': key, 'when': promised.isoformat() if promised else None, 'amount': money(value), 'source': 'Toast'})
            continue
        active = [c for c in order.get('checks', []) if not c.get('voided') and not c.get('deleted')]
        if not active:
            continue
        count += 1
        checks += len(active)
        voids += sum(bool(c.get('voided')) for c in order.get('checks', []))
        total += value
        discount += sum((m[1] for m in metrics), ZERO)
        refunds += sum((m[2] for m in metrics), ZERO)
        opened = parsed(order.get('openedDate'))
        # Hour order begins at closeout, so after-midnight sales remain at the end.
        if opened:
            hourly[(opened.astimezone(ET).hour - closeout) % 24] += value
    return {'date': day.isoformat(), 'net_sales': money(total), 'orders': count, 'checks': checks,
            'average_check': money(total / checks) if checks else None,
            'discounts': money(discount), 'refunds': money(refunds), 'voids': voids,
            'hourly': [money(v) for v in hourly], 'hour_labels': [f'{(closeout+h)%24:02}:00' for h in range(24)],
            'upcoming': upcoming}


def labor_summary(entries, employees, now, sales, overtime_multiplier=None):
    names = {e['guid']: e.get('firstName', 'Employee') for e in employees if e.get('guid')}
    hours = cost = ZERO
    complete = True
    hours_complete = True
    clocked = []
    by_employee = {}
    for e in entries:
        if e.get('deleted'):
            continue
        guid = (e.get('employeeReference') or {}).get('guid', 'unknown')
        regular, overtime = e.get('regularHours'), e.get('overtimeHours')
        if e.get('outDate'):
            if regular is None or overtime is None:
                complete = False
                hours_complete = False
                continue
            worked = dec(regular) + dec(overtime)
            payable = dec(regular)
            if dec(overtime):
                if overtime_multiplier is None:
                    complete = False
                else:
                    payable += dec(overtime) * dec(overtime_multiplier)
        else:
            start = parsed(e.get('inDate'))
            if not start or start > now:
                complete = False
                hours_complete = False
                continue
            worked = dec((now.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()) / 3600
            for b in e.get('breaks', []):
                if b.get('paid') is False and b.get('inDate'):
                    b_end = min(parsed(b.get('outDate')) or now, now)
                    worked -= max(ZERO, dec((b_end.astimezone(timezone.utc) - parsed(b['inDate']).astimezone(timezone.utc)).total_seconds()) / 3600)
            worked = max(ZERO, worked)
            payable = worked
            # Running shifts can cross overtime tiers; only provide an estimate.
            clocked.append({'employee_id': guid, 'employee': names.get(guid, 'Employee ' + guid[:8]), 'since': e['inDate'], 'hours': money(worked)})
        hours += worked
        by_employee[guid] = by_employee.get(guid, ZERO) + worked
        if e.get('hourlyWage') is None:
            complete = False
        else:
            cost += payable * dec(e['hourlyWage'])
    return {'hours': money(hours), 'cost': money(cost) if complete else None,
            'percentage': percent(cost, sales) if complete else None, 'clocked_in': clocked,
            'estimated': bool(clocked), 'complete': complete, 'hours_complete': hours_complete,
            'employee_hours': {k: money(v) for k, v in by_employee.items()}}
