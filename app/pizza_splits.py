"""Conservative, non-mutating reconciliation of explicitly linked pizza selections."""
from collections import defaultdict
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP


def active(value):
    return not any(value.get(key) for key in ('voided','deleted','deferred'))


def decimal(value):
    result=Decimal(str(value))
    if not result.is_finite() or result<0:raise ValueError('Invalid nonnegative amount')
    return result


def reconcile_pizza_splits(order,mapping):
    """Only complete, identity/price/check-linked groups become integer units.

    Unknown or incomplete groups are left untouched for the caller to flag.
    No API writes, source mutations, guessing, or grouping by menu name alone.
    """
    checks={};records={};duplicate=False
    for check in order.get('checks') or []:
        # Never silently discard a voided split branch while proving a group.
        if any((line.get('item') or {}).get('guid') in mapping['items'] and line.get('splitOrigin')
               and (not active(check) or not active(line)) for line in check.get('selections') or []):
            return order,{'groups':0,'source_lines':0,'whole_pizzas':0}
        if not active(check):continue
        cid=check.get('guid')
        if cid in checks:duplicate=True
        if cid:checks[cid]=check
        for line in check.get('selections') or []:
            if not active(line) or line.get('selectionType') not in (None,'NONE'):continue
            if (line.get('item') or {}).get('guid') not in mapping['items']:continue
            sid=line.get('guid')
            if not sid:continue
            if sid in records:duplicate=True
            records[sid]=(check,line)
    if duplicate:return order,{'groups':0,'source_lines':0,'whole_pizzas':0}
    def root(sid):
        seen=set()
        while sid in records:
            if sid in seen:return None
            seen.add(sid)
            parent=(records[sid][1].get('splitOrigin') or {}).get('guid')
            if not parent:return sid
            sid=parent
        return None
    groups=defaultdict(set)
    for sid,(_,line) in records.items():
        if not line.get('splitOrigin'):continue
        origin=root(sid)
        if origin:groups[origin].update((origin,sid))
    # Include all descendants, including intermediate split generations.
    for origin in groups:
        groups[origin].update(sid for sid in records if root(sid)==origin)
    replacements={};removed=set();counts={'groups':0,'source_lines':0,'whole_pizzas':0}
    for origin,ids in groups.items():
        if len(ids)<2:continue
        root_check,original=records[origin];root_cid=root_check.get('guid')
        if not root_cid:continue
        item_id=(original.get('item') or {}).get('guid')
        try:
            rows=[records[sid] for sid in ids]
            if any((line.get('item') or {}).get('guid')!=item_id for _,line in rows):continue
            if any(line.get('displayName')!=original.get('displayName') for _,line in rows):continue
            size=mapping['items'][item_id]['size']
            if any(mapping['groups'].get((line.get('itemGroup') or {}).get('guid'),size)!=size for _,line in rows):continue
            # An excluded/voided child may mean the quantity is no longer whole.
            # All accepted live branches must stay within the same check family.
            valid=True
            for check,line in rows:
                cid=check.get('guid');seen=set()
                while cid!=root_cid:
                    if not cid or cid in seen or cid not in checks:valid=False;break
                    seen.add(cid);cid=(checks[cid].get('splitOrigin') or {}).get('guid')
                if not valid:break
            if not valid:continue
            quantities=[decimal(line.get('quantity')) for _,line in rows]
            if any(q<=0 for q in quantities):continue
            total=sum(quantities,Decimal(0));whole=total.to_integral_value(rounding=ROUND_HALF_UP)
            if whole<1 or abs(total-whole)>Decimal('0.000001'):continue
            unit=decimal(original.get('receiptLinePrice'))
            if any(decimal(line.get('receiptLinePrice'))!=unit for _,line in rows):continue
            gross=sum((decimal(line.get('preDiscountPrice')) for _,line in rows),Decimal(0))
            if abs(gross-unit*whole)>Decimal('0.01'):continue
            # Check each allocation as well, allowing a cent of currency rounding.
            if any(abs(decimal(line.get('preDiscountPrice'))-unit*q)>Decimal('0.01') for (_,line),q in zip(rows,quantities)):continue
        except (ValueError,TypeError,InvalidOperation):continue
        replacement=dict(original,quantity=int(whole),splitOrigin=None)
        replacements[origin]=replacement;removed.update(ids-{origin})
        counts['groups']+=1;counts['source_lines']+=len(ids);counts['whole_pizzas']+=int(whole)
    if not replacements:return order,counts
    revised=dict(order);revised['checks']=[]
    for check in order.get('checks') or []:
        revised_check=dict(check)
        revised_check['selections']=[replacements.get(line.get('guid'),line)
            for line in check.get('selections') or [] if line.get('guid') not in removed]
        revised['checks'].append(revised_check)
    return revised,counts
