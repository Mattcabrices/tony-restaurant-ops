"""Read-only summaries of imported Clover aggregate history."""
from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from .calculations import reporting_week_start
from .pmix import product


def dates(start, end, max_days=730):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last-first).days >= max_days:
        raise ValueError(f'Clover history range must be 1–{max_days} days.')
    return first, last


def coverage(db, first, last):
    return db.rows('SELECT day,paid_orders,gross_line_cents,custom_lines,refunded_orders,imported_at '
                   'FROM clover_days WHERE day>=? AND day<=? ORDER BY day',
                   (first.isoformat(), last.isoformat()))


def sales_history(db, start, end, group_by='week', start_weekday=2):
    first, last = dates(start, end, 731)
    if group_by not in {'day', 'week', 'month'}:
        raise ValueError('group_by must be day, week, or month.')
    if group_by == 'day' and (last-first).days > 92:
        raise ValueError('Daily Clover history is limited to 93 days per query.')
    rows = coverage(db, first, last)
    grouped = defaultdict(lambda: {'paid_orders': 0, 'gross_line_cents': 0, 'days': 0})
    for row in rows:
        day = date.fromisoformat(row['day'])
        key = row['day'] if group_by == 'day' else (
            reporting_week_start(day, start_weekday).isoformat() if group_by == 'week' else day.strftime('%Y-%m'))
        group = grouped[key]
        group['paid_orders'] += row['paid_orders']
        group['gross_line_cents'] += row['gross_line_cents']
        group['days'] += 1
    return {'source': 'Clover historical orders, locally imported aggregates',
            'metric': 'Approximate gross line-item sales before final Clover report adjustments; USD',
            'start': start, 'end': end, 'group_by': group_by,
            'coverage_days': len(rows), 'requested_days': (last-first).days+1,
            'complete': len(rows) == (last-first).days+1,
            'periods': [{'period': key, 'days': value['days'],
                         'paid_orders': value['paid_orders'],
                         'gross_line_sales': round(value['gross_line_cents']/100, 2)}
                        for key, value in sorted(grouped.items())],
            'limitations': ['Clover gross line-item sales are not Toast net sales and may differ slightly from Clover dashboard gross sales.',
                            'Open, test, and fully refunded orders are excluded; identifiable inventory-linked top-level items only are counted in item mix.',
                            'Periods with missing coverage are partial; do not describe them as full-period totals.']}


def item_history(db, start, end, query=''):
    first, last = dates(start, end, 93)
    day_rows = coverage(db, first, last)
    rows = db.rows('SELECT item_id,name,SUM(quantity) AS quantity,SUM(gross_line_cents) AS gross_line_cents '
                   'FROM clover_item_days WHERE day>=? AND day<=? GROUP BY item_id,name',
                   (start, end))
    terms = [term.casefold() for term in query.split() if term]
    if terms:
        rows = [row for row in rows if all(term in row['name'].casefold() for term in terms)]
    rows.sort(key=lambda row: (-row['quantity'], row['name'].casefold()))
    recognized = [(row, *product(row['name'])) for row in rows]
    known_pieces = sum(row['quantity'] * size for row, category, size in recognized if category and size)
    unknown_size = sum(row['quantity'] for row, category, size in recognized if category and size is None)
    selected = rows[:40]
    output = []
    for row in selected:
        category, size = product(row['name'])
        output.append({'name': row['name'], 'quantity': row['quantity'],
                       'gross_line_sales': round(row['gross_line_cents']/100, 2),
                       'category': category, 'pieces_per_order': size,
                       'known_pieces': row['quantity']*size if size else None})
    return {'source': 'Clover historical top-level inventory-linked line items',
            'start': start, 'end': end, 'query': query, 'coverage_days': len(day_rows),
            'requested_days': (last-first).days+1,
            'complete': len(day_rows) == (last-first).days+1,
            'matching_quantity': sum(row['quantity'] for row in rows),
            'matching_items': len(rows), 'items': output,
            'known_wing_or_finger_pieces': known_pieces,
            'unknown_size_wing_or_finger_orders': unknown_size,
            'unlinked_custom_lines': sum(row['custom_lines'] for row in day_rows),
            'limitations': ['Counts represent top-level Clover inventory-linked line items, not package contents or modifiers.',
                            'Custom Item and other unlinked lines cannot be attributed to a menu item; this may undercount requested products.',
                            'Refunded item lines are excluded where flagged. Counts may differ from the Clover dashboard; verify critical decisions there.']}


def pizza_size(name):
    """Shared whole-pizza classification; slices/calzones never enter this count."""
    parts = name.split()
    if parts and parts[0].casefold() == 'halloween':
        parts = parts[1:]
    size = parts[0] if parts else ''
    if size == 'Large':
        size = 'Lrg'
    return size if size in {'Lrg', 'Med', 'Mini', 'GF'} and 'calzone' not in name.casefold() else None


def pizza_history(db, day):
    """Count Clover whole pizzas by menu size; keep slices separate."""
    first, last = dates(day, day, 1)
    day_rows = coverage(db, first, last)
    rows = db.rows('SELECT name,SUM(quantity) AS quantity FROM clover_item_days WHERE day=? GROUP BY name', (day,))
    sizes = {'Lrg': 0, 'Med': 0, 'Mini': 0, 'GF': 0}
    matched = []
    slices = 0
    for row in rows:
        name, quantity = row['name'], row['quantity']
        if name.casefold() == 'slice':
            slices += quantity
            continue
        size = pizza_size(name)
        if size:
            sizes[size] += quantity
            matched.append({'name': name, 'quantity': quantity})
    return {'source': 'Clover historical top-level inventory-linked line items',
            'business_date': day, 'whole_pizzas': sum(sizes.values()), 'by_size': sizes,
            'pizza_slices_separate': slices, 'matching_items': matched,
            'coverage_days': len(day_rows), 'complete': len(day_rows) == 1,
            'unlinked_custom_lines': sum(row['custom_lines'] for row in day_rows),
            'limitations': ['Whole pizzas are Clover items named Lrg/Large, Med, Mini, or GF, including Halloween-labeled specials; calzones are excluded and Slice is separate.',
                            'Top-level inventory-linked lines exclude package contents, modifiers, and unlinked Custom Item lines.',
                            'This is the Clover business date, which may differ from a calendar-day event around closeout.']}


def _portion_category(db, day, category):
    rows = db.rows('SELECT name,SUM(quantity) AS quantity FROM clover_item_days WHERE day=? GROUP BY name', (day,))
    matched = [(row, product(row['name'])[1]) for row in rows if product(row['name'])[0] == category]
    return {'portions': sum(row['quantity'] for row, _ in matched),
            'known_pieces': sum(row['quantity'] * size for row, size in matched if size),
            'unknown_size_portions': sum(row['quantity'] for row, size in matched if size is None),
            'items': [{'name': row['name'], 'portions': row['quantity'], 'pieces_per_portion': size}
                      for row, size in matched]}


def annual_holiday_mix(db, month_day, count, today):
    """Compare the most recent completed occurrences of a calendar date."""
    if not 1 <= count <= 5:
        raise ValueError('Compare one to five completed years.')
    try:
        month, day = (int(part) for part in month_day.split('-'))
        date(2000, month, day)
    except (TypeError, ValueError) as error:
        raise ValueError('Use MM-DD for the calendar date.') from error
    years = []
    for year in range(today.year, 2019, -1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            continue
        day_text = candidate.isoformat()
        pizzas = pizza_history(db, day_text)
        years.append({'business_date': day_text, 'weekday': candidate.strftime('%A'),
                      'complete': pizzas['complete'], 'whole_pizzas': pizzas['whole_pizzas'],
                      'pizza_by_size': pizzas['by_size'], 'slices_separate': pizzas['pizza_slices_separate'],
                      'wings': _portion_category(db, day_text, 'wings'),
                      'fingers': _portion_category(db, day_text, 'fingers'),
                      'unlinked_custom_lines': pizzas['unlinked_custom_lines']})
        if len(years) == count:
            break
    return {'source': 'Imported Clover top-level inventory-linked line items',
            'calendar_date': month_day, 'years': years,
            'limitations': ['Only completed business dates with imported Clover coverage can support a count; missing coverage is not zero.',
                            'Whole pizzas include Lrg/Large, Med, Mini, and GF items, including Halloween specials; slices are separate.',
                            'Wing/finger pieces are totals only when unknown_size_portions is zero.',
                            'Package contents, modifiers, and unlinked Custom Item lines cannot be classified.',
                            'Same calendar date can have a different weekday, game schedule, or promotion each year.']}


def seasonal_context(db, target):
    """Historical gross-sale context for the same weekday near this date in prior years."""
    weekday = target.weekday()
    all_rows = db.rows('SELECT day,gross_line_cents,paid_orders FROM clover_days '
                       'WHERE gross_line_cents>0 AND paid_orders>0 ORDER BY day')
    if not all_rows:
        return None
    candidates = []
    annual = []
    for row in all_rows:
        day = date.fromisoformat(row['day'])
        if day.weekday() != weekday:
            continue
        annual.append(row['gross_line_cents'])
        try:
            anchor = date(day.year, target.month, target.day)
        except ValueError:
            continue
        if abs((day-anchor).days) <= 21:
            candidates.append(row)
    if len(candidates) < 4 or len(annual) < 12:
        return None
    seasonal = median(row['gross_line_cents'] for row in candidates)
    baseline = median(annual)
    return {'source': 'Imported Clover gross line-item sales',
            'same_weekday_near_calendar_date_days': len(candidates),
            'historical_gross_median': round(seasonal/100, 2),
            'relative_to_all_historical_same_weekdays_percent': round((seasonal/baseline-1)*100, 1) if baseline else None,
            'historical_range': [min(row['day'] for row in candidates), max(row['day'] for row in candidates)],
            'limitations': 'Gross-to-net differences and the POS switch prevent direct substitution for the Toast forecast. This is seasonal context, not a causal game uplift.'}
