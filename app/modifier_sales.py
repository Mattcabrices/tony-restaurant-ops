"""Toast modifier selections, kept separate from top-level menu-item counts."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .calculations import parsed
from .item_sales import number, terms


def modifier_sales_summary(orders_by_day, names, now, query='', parent_query=''):
    wanted, parent_wanted = terms(query), terms(parent_query)
    counts = defaultdict(lambda: Decimal(0))
    seen_orders, seen_parents = set(), set()
    orders_by_modifier = defaultdict(set)
    duplicate_parents = split_parents = missing_names = 0
    scanned = 0

    def active(value):
        return not (value.get('deleted') or value.get('voided') or value.get('deferred'))

    def quantity(value):
        try:
            result = Decimal(str(value['quantity']))
        except (KeyError, TypeError, InvalidOperation):
            raise ValueError('Active modifier has no valid quantity.') from None
        if not result.is_finite() or result < 0:
            raise ValueError('Active modifier has no valid quantity.')
        return result

    for day, orders in orders_by_day:
        for index, order in enumerate(orders):
            order_key = (day, order.get('guid') or f'unguided:{index}')
            if order_key in seen_orders:
                continue
            seen_orders.add(order_key)
            scanned += 1
            if not active(order) or order.get('excessFood') or order.get('approvalStatus') == 'FUTURE':
                continue
            promised = parsed(order.get('promisedDate') or order.get('estimatedFulfillmentDate'))
            if promised and promised > now:
                continue
            for check in order.get('checks') or []:
                if not active(check):
                    continue
                for parent in check.get('selections') or []:
                    if not active(parent) or parent.get('selectionType') not in (None, 'NONE'):
                        continue
                    parent_id = parent.get('guid')
                    if parent_id and (day, parent_id) in seen_parents:
                        duplicate_parents += 1
                        continue
                    if parent_id:
                        seen_parents.add((day, parent_id))
                    split_parents += bool(parent.get('splitOrigin'))
                    parent_name = parent.get('displayName') or ''
                    if not parent_name:
                        raise ValueError('Active parent selection has no display name.')
                    if parent_wanted and not parent_wanted <= terms(parent_name):
                        continue
                    # A Toast modifier quantity already reflects its parent's selected
                    # quantity. The same modifier may be selected twice intentionally.
                    def visit(modifiers, depth):
                        nonlocal missing_names
                        if depth > 12:
                            raise ValueError('Toast modifier nesting exceeds safety limit.')
                        for mod in modifiers or []:
                            if not active(mod):
                                continue
                            mod_name = mod.get('displayName') or names.get((mod.get('item') or {}).get('guid')) or ''
                            group = names.get((mod.get('optionGroup') or {}).get('guid')) or 'Unclassified'
                            if not mod_name:
                                missing_names += 1
                                mod_name = 'Unnamed modifier'
                            amount = quantity(mod)
                            key = (mod_name, group, parent_name, depth)
                            counts[key] += amount
                            orders_by_modifier[key].add(order_key)
                            visit(mod.get('modifiers'), depth + 1)
                    visit(parent.get('modifiers'), 1)

    phrase = ' '.join(query.casefold().split())
    phrase_keys = {key for key in counts if phrase and phrase in ' '.join((key[0] + ' ' + key[1]).casefold().split())}
    selected = {key for key in counts if (key in phrase_keys if phrase_keys else
                                         not wanted or wanted <= terms(key[0] + ' ' + key[1]))}
    matched_orders = set().union(*(orders_by_modifier[key] for key in selected)) if selected else set()
    matches = [{'modifier': mod, 'option_group': group, 'parent_item': parent,
                'depth': depth, 'selections': number(amount)}
               for (mod, group, parent, depth), amount in counts.items()
               if (mod, group, parent, depth) in selected]
    matches.sort(key=lambda x: (-x['selections'], x['modifier'].casefold(), x['parent_item'].casefold()))
    total = sum((Decimal(str(row['selections'])) for row in matches), Decimal(0))
    dates = [day.isoformat() for day, _ in orders_by_day]
    exact = not (duplicate_parents or split_parents or missing_names)
    return {'start_date': min(dates), 'end_date': max(dates), 'query': query,
            'parent_query': parent_query, 'matched_selections': number(total) if exact else None,
            'observed_matching_selections': number(total), 'exact': exact,
            'modifiers': matches[:50 if wanted else 25], 'truncated': len(matches) > (50 if wanted else 25),
            'orders_scanned': scanned, 'orders_with_matches': len(matched_orders),
            'duplicate_parent_lines': duplicate_parents, 'split_parent_lines': split_parents,
            'unnamed_modifier_lines': missing_names,
            'limitations': [
                'These are Toast modifier selections, not menu items sold or measured ingredient usage.',
                'Counts use recorded modifier quantity once, including nested modifiers; package choices may appear here.',
                'Voided, deleted, deferred, excess-food, and future orders/selections are excluded.',
                'Refunded units are not subtracted because refund amounts do not establish modifier quantities.',
                'Names and option groups use the current Toast menu when missing from orders; historical names may differ.',
            ]}
