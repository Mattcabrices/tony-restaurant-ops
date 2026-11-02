"""Explicit Toast whole-pizza identities; packages/modifiers are never pizzas."""
from collections import Counter
from decimal import Decimal,InvalidOperation
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from .storage import ROOT
import re

from .calculations import business_day,parsed
from .item_sales import number
from .pizza_splits import reconcile_pizza_splits

SIZES=('Lrg','Med','Mini','GF')
PREFIX={'large':'Lrg','medium':'Med','mini':'Mini','gf':'GF'}
EXCLUDED=re.compile(r'\b(package|packages|catering|slice|slices|calzone|calzones|topping|toppings|sauce)\b',re.I)


def whole_pizza_query(query):
    tokens=set(re.findall(r'[a-z0-9]+',query.casefold()))
    allowed={'whole','pizza','pizzas','large','medium','mini','gf','gluten','free'}
    if not tokens & {'pizza','pizzas'} or not tokens<=allowed:return None
    sizes={PREFIX[x] for x in tokens if x in PREFIX}
    if 'gluten' in tokens:sizes.add('GF')
    if len(sizes)>1:return None
    return next(iter(sizes),'all')


@lru_cache(maxsize=1)
def load_mapping():
    raw=(ROOT/'data'/'toast_pizza_mapping.json').read_bytes()
    value=json.loads(raw)
    if not value.get('items') or not value.get('groups'):
        raise ValueError('Whole-pizza mapping is unavailable.')
    if any(x.get('size') not in SIZES for x in value['items'].values()) or any(x not in SIZES for x in value['groups'].values()):
        raise ValueError('Invalid whole-pizza size mapping.')
    return value,hashlib.sha256(raw).hexdigest()


def whole_pizza_summary(orders,day,observed_at,closeout,mapping=None,group_names=None):
    return _pizza_summary(orders,day,observed_at,closeout,mapping,group_names,booked=False)


def booked_whole_pizza_summary(orders,day,observed_at,closeout,mapping=None,group_names=None):
    """Caller supplies only live orders scoped to their scheduled fulfillment day.

    This snapshot counts booked units, never closed sales or research observations.
    Identity, item exclusions and linked-split checks are shared with sales counts.
    """
    return _pizza_summary(orders,day,observed_at,closeout,mapping,group_names,booked=True)


def _pizza_summary(orders,day,observed_at,closeout,mapping,group_names,*,booked):
    if mapping is None:mapping,mapping_sha=load_mapping()
    else:mapping_sha=hashlib.sha256(json.dumps(mapping,sort_keys=True).encode()).hexdigest()
    totals={size:Decimal(0) for size in SIZES};issues=Counter();items=Counter()
    if not orders and not booked:issues['unconfirmed_empty_day']+=1
    group_names=group_names or {}
    seen_orders=set();seen_selections=set();reconciled=Counter()
    for order in orders:
        if order.get('deleted') or order.get('voided') or order.get('excessFood'):continue
        if not booked and order.get('approvalStatus')=='FUTURE':continue
        promised=parsed(order.get('promisedDate') or order.get('estimatedFulfillmentDate'))
        if not booked and promised and promised>observed_at:continue
        if not booked and order.get('businessDate') is not None and str(order['businessDate'])!=day.strftime('%Y%m%d'):
            issues['unexpected_business_date']+=1;continue
        guid=order.get('guid')
        if not guid:issues['missing_order_ids']+=1
        if guid and guid in seen_orders:
            issues['duplicate_orders']+=1;continue
        if guid:seen_orders.add(guid)
        order,split_counts=reconcile_pizza_splits(order,mapping)
        reconciled.update(split_counts)
        for check in order.get('checks') or []:
            if check.get('deleted') or check.get('voided'):continue
            for item in check.get('selections') or []:
                if item.get('deleted') or item.get('voided') or item.get('deferred') or item.get('selectionType') not in (None,'NONE'):continue
                name=item.get('displayName') or ''
                identity=mapping['items'].get((item.get('item') or {}).get('guid'))
                group_id=(item.get('itemGroup') or {}).get('guid')
                group_size=mapping['groups'].get(group_id)
                group_name=group_names.get(group_id,'')
                if EXCLUDED.search(name+' '+group_name):
                    if identity:issues['mapped_identity_name_conflict']+=1
                    continue
                if group_names and not group_name and not identity:
                    issues['unclassified_top_level_items']+=1
                prefix=PREFIX.get(name.split()[0].casefold()) if name.split() else None
                candidate=bool(identity or group_size or (prefix and not group_name) or re.search(r'\bpizza\b',name,re.I))
                if not candidate:continue
                sid=item.get('guid')
                if not sid:issues['missing_pizza_selection_ids']+=1
                if sid and sid in seen_selections:
                    issues['duplicate_pizza_selections']+=1;continue
                if sid:seen_selections.add(sid)
                if not identity:
                    issues['unmapped_pizza_selections']+=1;continue
                size=identity['size']
                if prefix!=size or (group_size and group_size!=size):
                    issues['mapped_identity_name_conflict']+=1;continue
                if item.get('splitOrigin'):issues['split_pizza_selections']+=1
                try:quantity=Decimal(str(item['quantity']))
                except (KeyError,InvalidOperation,TypeError,ValueError):
                    issues['invalid_pizza_quantity']+=1;continue
                if not quantity.is_finite() or quantity<0 or quantity!=quantity.to_integral_value():
                    issues['invalid_pizza_quantity']+=1;continue
                totals[size]+=quantity
                items[(name,size)]+=quantity
    if not booked and day>=business_day(observed_at,closeout):issues['business_day_not_closed']+=1
    exact=not issues
    return {'business_date':day.isoformat(),'source':'Toast top-level orders and explicit menu identity mapping',
        'calculation_version':'booked-whole-pizza-v1' if booked else 'whole-pizza-v2-linked-splits','reconciliation':dict(reconciled),
        'mapping_version':mapping['version'],'mapping_sha256':mapping_sha,'mapping_verified_at':mapping.get('verified_at'),
        'observed_quantity':number(sum(totals.values(),Decimal(0))),
        'quantity':number(sum(totals.values(),Decimal(0))) if exact else None,
        'by_size':{size:number(value) for size,value in totals.items()},'quality':'complete' if exact else 'partial',
        'items':[{'name':name,'size':size,'quantity':number(value)} for (name,size),value in sorted(items.items())],
        'issues':dict(issues),'orders_scanned':len(orders),
        'limitations':['Ordered top-level whole pizzas only; packages, slices, calzones and modifiers excluded.',
            'Refunded units are not subtracted: refund amounts do not establish refunded item quantities.',
            'Current menu identity mapping is not proof that all historical menu items are covered. Only explicitly linked, check-family/price/quantity-verified split groups reconcile; other split/duplicate or invalid records withhold the exact total.']}
