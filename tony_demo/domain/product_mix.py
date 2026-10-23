"""Top-level wing and finger product mix with explicit unknown sizes."""
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .calculations import parsed


PIECES_PER_PORTION = {}  # No private serving-build overrides are distributed.
PIECE_PATTERN = re.compile(r'\b(\d{1,3})\s*(?:pc|pcs|piece|pieces|ct|count)?\s*(?:chicken\s+)?(wings?|fingers?)\b', re.I)


def product(name):
    lowered = name.casefold().strip()
    if re.search(r'\bwing(?:s)?\b', lowered) and not re.search(r'\b(sauce|dip|dressing)\b', lowered):
        category = 'wings'
    elif re.search(r'\bfinger(?:s)?\b', lowered):
        category = 'fingers'
    else:
        return None, None
    match = PIECE_PATTERN.search(name)
    size = int(match.group(1)) if match and ((category == 'wings' and match.group(2).lower().startswith('wing')) or
                                               (category == 'fingers' and match.group(2).lower().startswith('finger'))) else PIECES_PER_PORTION.get(lowered)
    return category, size


def number(value):
    return int(value) if value == value.to_integral_value() else float(value)


def product_mix(orders, group_names, now):
    items = defaultdict(lambda: {'portions': Decimal('0'), 'known_pieces': Decimal('0'), 'pieces_per_portion': None})
    order_ids = {'wings': set(), 'fingers': set()}
    seen_orders, seen_selections = set(), set()
    duplicates = splits = unknown_groups = 0
    for index, order in enumerate(orders):
        order_id = order.get('guid') or f'unguided:{index}'
        if order.get('guid') in seen_orders and order.get('guid') is not None:
            continue
        seen_orders.add(order.get('guid'))
        promised = parsed(order.get('promisedDate') or order.get('estimatedFulfillmentDate'))
        if (order.get('deleted') or order.get('voided') or order.get('excessFood') or
                order.get('approvalStatus') == 'FUTURE' or (promised and promised > now)):
            continue
        for check in order.get('checks') or []:
            if check.get('deleted') or check.get('voided'):
                continue
            for selection in check.get('selections') or []:
                if (selection.get('deleted') or selection.get('voided') or selection.get('deferred') or
                        selection.get('selectionType') not in (None, 'NONE')):
                    continue
                selection_id = selection.get('guid')
                if selection_id and selection_id in seen_selections:
                    duplicates += 1
                    continue
                if selection_id:
                    seen_selections.add(selection_id)
                name = selection.get('displayName') or ''
                category, size = product(name)
                if not category:
                    continue
                if selection.get('splitOrigin'):
                    splits += 1
                group = group_names.get((selection.get('itemGroup') or {}).get('guid'))
                if not group:
                    unknown_groups += 1
                    group = 'Unclassified'
                try:
                    quantity = Decimal(str(selection['quantity']))
                except (KeyError, TypeError, InvalidOperation):
                    raise ValueError('Wing or finger selection has no valid quantity.') from None
                if not quantity.is_finite() or quantity < 0:
                    raise ValueError('Wing or finger selection has no valid quantity.')
                key = (category, name, group)
                items[key]['portions'] += quantity
                items[key]['pieces_per_portion'] = size
                if size is not None:
                    items[key]['known_pieces'] += quantity * size
                order_ids[category].add(order_id)
    exact = not (duplicates or splits or unknown_groups)
    result = {}
    for category in ('wings', 'fingers'):
        rows = []
        for (kind, name, group), data in items.items():
            if kind == category:
                rows.append({'name': name, 'menu_group': group, 'portions': number(data['portions']),
                             'pieces_per_portion': data['pieces_per_portion'],
                             'known_pieces': number(data['known_pieces'])})
        rows.sort(key=lambda row: (-row['portions'], row['name'].casefold()))
        portions = sum((items[(category, row['name'], row['menu_group'])]['portions'] for row in rows), Decimal('0'))
        known = sum((items[(category, row['name'], row['menu_group'])]['known_pieces'] for row in rows), Decimal('0'))
        unknown = sum((items[(category, row['name'], row['menu_group'])]['portions'] for row in rows
                       if row['pieces_per_portion'] is None), Decimal('0'))
        result[category] = {'portion_orders': number(portions) if exact else None,
                            'observed_portion_orders': number(portions), 'guest_orders': len(order_ids[category]),
                            'total_pieces': number(known) if exact and not unknown else None,
                            'known_pieces': number(known), 'unknown_size_portions': number(unknown),
                            'items': rows}
    return {'exact': exact, 'categories': result, 'orders_scanned': len(orders),
            'duplicate_selection_lines': duplicates, 'split_selection_lines': splits,
            'unclassified_matching_lines': unknown_groups,
            'limitations': ['Counts use top-level Toast menu selections only; modifiers and catering-package contents are excluded.',
                            'A named portion (for example, 10 Wings) contributes its Toast quantity in portions and quantity times 10 in pieces.',
                            'Refunded units are not subtracted because refund amounts do not establish refunded item quantities.',
                            'Unknown sizes withhold an exact total-pieces count; duplicate, split, or unclassified matching selections withhold exact counts.']}
