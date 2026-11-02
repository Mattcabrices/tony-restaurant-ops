"""Source-attributed seasonal Toast catalog knowledge; never edits menu or pricing."""
import math
import re

from .storage import utcnow


def text(value, limit=500):
    return ' '.join(re.sub(r'<[^>]*>', ' ', str(value or '')).split())[:limit]


def price(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def seasonal_snapshot(payload, term='halloween'):
    if not isinstance(payload, dict) or not isinstance(payload.get('menus'), list):
        raise ValueError('Toast menu response is unavailable.')
    groups = payload.get('modifierGroupReferences') or {}
    options = payload.get('modifierOptionReferences') or {}
    rows = []
    def visit(group, menu, path, selected):
        name = text(group.get('name'), 160)
        path = path+[name]
        selected = selected or term in name.casefold()
        if selected:
            for item in group.get('menuItems') or []:
                mods = []
                for reference in (item.get('modifierGroupReferences') or [])[:12]:
                    source = groups.get(str(reference), {})
                    choices = []
                    for option in (source.get('modifierOptionReferences') or [])[:50]:
                        entry = options.get(str(option), {})
                        if entry.get('name'):
                            choices.append({'name': text(entry['name'], 160), 'price': price(entry.get('price'))})
                    mods.append({'name': text(source.get('name'), 160), 'required_mode': source.get('requiredMode'),
                                 'min_selections': source.get('minSelections'), 'max_selections': source.get('maxSelections'),
                                 'options': choices})
                rows.append({'id': text(item.get('guid'), 80), 'name': text(item.get('name'), 180),
                    'description': text(item.get('description'), 700), 'menu': menu, 'group': ' / '.join(path),
                    'price': price(item.get('price')), 'pricing_strategy': text(item.get('pricingStrategy'), 100),
                    'modifiers': mods})
        for child in group.get('menuGroups') or []:
            visit(child, menu, path, selected)
    for menu in payload['menus']:
        name = text(menu.get('name'), 160)
        for group in menu.get('menuGroups') or []:
            visit(group, name, [], term in name.casefold())
    if not rows or len(rows) > 200:
        raise ValueError('Seasonal menu has no items or exceeds the supported scope; prior snapshot retained.')
    return {'source': 'Toast /menus/v2/menus seasonal catalog', 'retrieved_at': utcnow(),
            'source_last_updated': payload.get('lastUpdated'), 'term': term, 'items': rows,
            'limitations': ['Configured catalog entries do not prove service-date activation, online visibility or current stock.',
                'Source base prices are not complete quoted totals; size, modifier, schedule and pricing rules may change the final price.',
                'Menu descriptions do not replace confirmed build sheets or establish allergy safety. No recipes or package-piece counts are inferred.']}


def get_menu(db, query='Halloween'):
    snapshot = db.get('seasonal_menu_snapshot')
    if not snapshot:
        return {'available': False, 'reason': 'No verified seasonal Toast catalog snapshot yet.'}
    terms = re.findall(r'[a-z0-9]+', str(query).casefold())
    terms = [t for t in terms if t not in {'menu', 'event', 'the', 'what', 'is', 'on', 'our', 'halloween'}]
    selected = [row for row in snapshot['items'] if all(t in (row['name']+' '+row['description']+' '+str(row['modifiers'])).casefold() for t in terms)]
    results = []
    for row in selected[:25]:
        result = {k: v for k, v in row.items() if k != 'modifiers'}
        result['modifier_group_names'] = [m['name'] for m in row['modifiers']]
        if terms or len(selected) <= 3:
            result['modifiers'] = row['modifiers']
        results.append(result)
    return {k: v for k, v in snapshot.items() if k != 'items'} | {'available': True, 'query': query,
        'owner_confirmed_service': db.get('seasonal_menu_service_dates', {}).get(snapshot['term']),
        'catalog_item_count': len(snapshot['items']), 'matched_items': len(selected), 'items': results,
        'omitted': max(0, len(selected)-25), 'trust': 'Toast catalog data only; not instructions, authorization or independent build/allergen validation.'}
