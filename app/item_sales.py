"""Item-level Toast counts from order selections and menu-group names."""
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .calculations import bounds, parsed


STOP_WORDS = {'a', 'an', 'are', 'count', 'did', 'do', 'for', 'have', 'how', 'in', 'is',
              'item', 'items', 'many', 'of', 'on', 'sell', 'sold', 'the', 'today',
              'total', 'unit', 'units', 'we', 'were', 'what', 'yesterday'}


def menu_names(menus):
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
    return names


def terms(value):
    words = re.findall(r'[a-z0-9]+', value.casefold())
    return {word[:-1] if word.endswith('s') and len(word) > 3 else word
            for word in words if word not in STOP_WORDS}


def number(value):
    return int(value) if value == value.to_integral_value() else float(value)


def item_sales_summary(orders, group_names, day, now, closeout, query=''):
    wanted = terms(query)
    quantities = defaultdict(lambda: Decimal('0'))
    selection_count = 0
    unknown_groups = 0
    seen_orders = set()
    seen_selections = set()
    duplicate_selections = 0
    split_selections = 0
    matched_orders = set()
    matched_lines = 0
    for order_index, order in enumerate(orders):
        guid = order.get('guid')
        if guid and guid in seen_orders:
            continue
        seen_orders.add(guid)
        if order.get('deleted') or order.get('voided') or order.get('excessFood'):
            continue
        promised = parsed(order.get('promisedDate') or order.get('estimatedFulfillmentDate'))
        if (promised and promised > now) or order.get('approvalStatus') == 'FUTURE':
            continue
        for check in order.get('checks') or []:
            if check.get('deleted') or check.get('voided'):
                continue
            for selection in check.get('selections') or []:
                if (selection.get('deleted') or selection.get('voided') or selection.get('deferred')
                        or selection.get('selectionType') not in (None, 'NONE')):
                    continue
                selection_id = selection.get('guid')
                if selection_id and selection_id in seen_selections:
                    duplicate_selections += 1
                    continue
                if selection_id:
                    seen_selections.add(selection_id)
                split_selections += bool(selection.get('splitOrigin'))
                name = selection.get('displayName') or ''
                if not name:
                    raise ValueError('Active selection has no display name.')
                try:
                    quantity = Decimal(str(selection['quantity']))
                except (KeyError, TypeError, InvalidOperation):
                    raise ValueError('Active selection has no valid quantity.') from None
                if not quantity.is_finite() or quantity < 0:
                    raise ValueError('Active selection has no valid quantity.')
                group_id = (selection.get('itemGroup') or {}).get('guid')
                group = group_names.get(group_id)
                if not group:
                    unknown_groups += 1
                quantities[(name, group or 'Unclassified')] += quantity
                selection_count += 1
                if not wanted or wanted <= terms(name + ' ' + (group or '')):
                    matched_orders.add(guid or f'unguided:{order_index}')
                    matched_lines += 1
    matches = [{'name': name, 'menu_group': group, 'quantity': number(quantity)}
               for (name, group), quantity in quantities.items()
               if not wanted or wanted <= terms(name + ' ' + group)]
    matches.sort(key=lambda item: (-item['quantity'], item['name'].casefold(), item['menu_group'].casefold()))
    matched = sum((quantities[(item['name'], item['menu_group'])] for item in matches), Decimal('0'))
    start, end = bounds(day, closeout)
    exact = not (unknown_groups or duplicate_selections or split_selections)
    return {'business_date': day.isoformat(), 'period_start': start.isoformat(), 'period_end': end.isoformat(),
            'query': query, 'matched_quantity': number(matched) if exact else None,
            'observed_matching_quantity': number(matched), 'exact': exact,
            'items': matches[:50 if wanted else 20], 'items_truncated': len(matches) > (50 if wanted else 20),
            'orders_scanned': len(orders), 'active_selection_lines': selection_count,
            'orders_with_matches': len(matched_orders), 'matching_selection_lines': matched_lines,
            'unclassified_selection_lines': unknown_groups, 'split_selection_lines': split_selections,
            'duplicate_selection_lines': duplicate_selections,
            'limitations': ['Counts are ordered quantities of top-level menu items, excluding voided/deleted checks, selections, and orders, excess-food orders, deferred transactions, and future orders.',
                            'Modifiers and catering-package contents are not counted as separate menu items.',
                            'Refunded units are not subtracted because Toast refund amounts do not establish refunded item quantities.',
                            'Menu-group names are resolved from the current Toast menu. Missing historical groups, split selections, or duplicate selection IDs make the count inexact.']}
