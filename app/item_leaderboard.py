"""Covered-history item records, using stored aggregates only. No network calls."""
from collections import defaultdict
from datetime import date,datetime,timedelta
import re
import json

from .calculations import ET
from .clover_history import pizza_size
from .pmix import product
from .corner.item_quality import latest_counts


def classify_query(query):
    q=' '.join(query.casefold().split())
    if not q or len(q)>80:raise ValueError('Use an item query of 1–80 characters.')
    tokens=set(re.findall(r'[a-z0-9]+',q))
    if tokens & {'slice','slices'} and tokens<= {'pizza','pizzas','slice','slices'}:return 'slices',None
    if tokens & {'pizza','pizzas'}:
        if not tokens<= {'whole','pizza','pizzas','large','lrg','medium','med','mini','gf','gluten','free'}:
            raise ValueError('Whole-pizza leaderboard supports pizza or one size; flavor combinations need verified mapping.')
        sizes={'large':'Lrg','lrg':'Lrg','medium':'Med','med':'Med','mini':'Mini','gf':'GF','gluten':'GF'}
        if len({value for key,value in sizes.items() if key in tokens})>1:
            raise ValueError('Use one pizza size per leaderboard.')
        size=next((value for key,value in sizes.items() if key in tokens),None)
        return 'pizza',size
    if tokens & {'wing','wings','finger','fingers'}:
        if (tokens & {'wing','wings'}) and (tokens & {'finger','fingers'}):
            raise ValueError('Rank one category at a time.')
        if not all(t.isdigit() or t in {'wing','wings','finger','fingers','chicken','order','orders','piece','pieces'} for t in tokens):
            raise ValueError('Use wings, fingers, or one numeric menu size for this leaderboard.')
        return ('wings' if tokens & {'wing','wings'} else 'fingers'),None
    return 'item',None


def rank_days(values,limit):
    ordered=sorted(((day,value) for day,value in values.items() if value>0),key=lambda x:(-x[1],x[0]))
    ranks=[];previous=None;rank=0
    for position,(day,value) in enumerate(ordered,1):
        if value!=previous:rank=position
        previous=value
        if len(ranks)<limit:ranks.append({'rank':rank,'business_date':day,'count':value})
    return ranks


def item_leaderboard(db,query,start='',end='',limit=5,unit='portions',corner=None,today=None):
    if type(limit) is not int or not 1<=limit<=10:raise ValueError('Limit must be 1–10.')
    if unit not in {'portions','pieces'}:raise ValueError('Use portions or pieces.')
    category,size=classify_query(query)
    if unit=='pieces' and category not in {'wings','fingers'}:
        raise ValueError('Pieces require wings or fingers; pizza counts are whole pizzas, not slices.')
    if len(re.findall(r'\b\d+\b',query))>1 and category in {'wings','fingers'}:
        raise ValueError('Use one wing/finger size per leaderboard.')
    today=today or datetime.now(ET).date()
    first=date.fromisoformat(start) if start else date(2000,1,1)
    last=date.fromisoformat(end) if end else today-timedelta(days=1)
    last=min(last,today-timedelta(days=1))
    if first>last:raise ValueError('Select a range containing a completed business day.')
    days=db.rows('SELECT day,custom_lines,imported_at FROM clover_days WHERE day>=? AND day<=? ORDER BY day',
                 (first.isoformat(),last.isoformat()))
    quantities=defaultdict(float);unknown=defaultdict(float)
    rows=db.rows('SELECT day,name,quantity FROM clover_item_days WHERE day>=? AND day<=?',
                 (first.isoformat(),last.isoformat()))
    covered={r['day'] for r in days}
    for row in rows:
        if row['day'] not in covered:continue
        name=row['name'];qty=row['quantity'];count=None
        if category=='pizza' and pizza_size(name) and (size is None or pizza_size(name)==size):count=qty
        elif category=='slices' and name.casefold()=='slice':count=qty
        elif category in {'wings','fingers'}:
            actual,pieces=product(name)
            if actual==category:
                # A size-specific query must not accidentally count all wing sizes.
                wanted=re.findall(r'\b\d+\b',query)
                if wanted and (pieces is None or pieces!=int(wanted[0])):continue
                if unit=='pieces':
                    if pieces is None:unknown[row['day']]+=qty
                    else:count=qty*pieces
                else:count=qty
        elif category=='item' and all(term in name.casefold() for term in query.casefold().split()):count=qty
        if count is not None:quantities[row['day']]+=count
    for day in unknown:quantities.pop(day,None)
    source={'source':'Clover imported top-level inventory-linked items','unit':'whole_pizzas' if category=='pizza' else 'slices' if category=='slices' else unit,
        'first_covered_day':days[0]['day'] if days else None,'last_covered_day':days[-1]['day'] if days else None,
        'imported_days':len(days),'freshness':max((r['imported_at'] for r in days),default=None),
        'unknown_size_days_excluded':len(unknown),'rankings':rank_days(quantities,limit),
        'unlinked_custom_lines':sum(r['custom_lines'] for r in days)}
    if days:
        span=(date.fromisoformat(days[-1]['day'])-date.fromisoformat(days[0]['day'])).days+1
        source['missing_dates_within_covered_span']=span-len(days)
    result={'query':query,'retrieved_at':datetime.now(ET).isoformat(),'scope':'Highest recorded counts in available covered history; never a lifetime record.',
        'requested_range':[start or 'all available',end or 'last completed business day'],
        'current_day_excluded':True,'sources':[source],
        'limitations':['Clover and Toast rankings remain separate; no combined winner is inferred.',
            'Clover excludes modifiers, package contents, and unlinked custom items. Recorded counts can understate actual totals.',
            'Imported dates do not guarantee source completeness. Missing dates are not zero; periods outside coverage are unavailable.',
            'Ties share rank; the requested result limit can truncate tied dates. This is a count ranking, not revenue or profit.']}
    # Only independently stored, explicitly complete aggregate observations qualify.
    if corner and category=='pizza':
        snapshots=latest_counts(corner.store,first.isoformat(),last.isoformat())
        valid=[r for r in snapshots if r['quality']=='complete' and r['quantity'] is not None]
        values={r['day']:json.loads(r['by_size'])[size] if size else r['quantity'] for r in valid}
        result['sources'].append({'source':'Toast verified whole-pizza snapshots','unit':'whole_pizzas',
            'first_covered_day':snapshots[0]['day'] if snapshots else None,'last_covered_day':snapshots[-1]['day'] if snapshots else None,
            'verified_aggregate_days':len(valid),'flagged_days_excluded':len(snapshots)-len(valid),
            'split_reconciled_days':sum(json.loads(r['reconciliation']).get('groups',0)>0 for r in valid),
            'flagged_date_sample':[r['day'] for r in snapshots if r not in valid][:14],
            'freshness':max((r['last_seen_at'] for r in snapshots),default=None),'rankings':rank_days(values,limit),
            'limitations':'Top-level ordered whole pizzas, excluding packages/modifiers/slices/calzones. Refunded units are not subtracted. Ambiguous or missing counts are excluded; coverage is not lifetime history.'})
        if not valid:result['toast_pending']='No unambiguous completed-day whole-pizza snapshots yet; flagged or uncollected days are not zero.'
    elif corner and category in {'wings','fingers'} and not re.findall(r'\b\d+\b',query):
        metric='toast.item_pieces' if unit=='pieces' else 'toast.item_portions'
        records=corner.store.rows('''SELECT o.day,o.value,o.quality,d.retrieved_at FROM observations o
            JOIN datasets d ON d.id=o.dataset_id WHERE o.metric=? AND o.dimension=? AND o.day>=? AND o.day<=?
            AND o.id=(SELECT MAX(x.id) FROM observations x WHERE x.day=o.day AND x.metric=o.metric AND x.dimension=o.dimension)
            ORDER BY o.day''',(metric,category,first.isoformat(),last.isoformat()))
        complete=[r for r in records if r['quality']=='complete']
        result['sources'].append({'source':"Toast stored Corner aggregates",'unit':unit,
            'first_covered_day':complete[0]['day'] if complete else None,'last_covered_day':complete[-1]['day'] if complete else None,
            'verified_aggregate_days':len(complete),'freshness':max((r['retrieved_at'] for r in complete),default=None),
            'rankings':rank_days({r['day']:r['value'] for r in complete},limit)})
    else:
        result['toast_pending']='No verified stored Toast leaderboard for this query. Whole-pizza mapping is not yet verified; menu-query totals must not be substituted. No bulk API collection was triggered.'
    return result
