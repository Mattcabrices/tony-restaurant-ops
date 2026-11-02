"""Bounded, read-only aggregate retrieval across existing POS sources.

No model calls, raw-order persistence, guessed missing dates or mixed POS totals.
"""
import asyncio
from datetime import date, datetime, timedelta, timezone
import json
import re

from .calculations import business_day, sales_summary
from .providers import ProviderError
from .item_sales import menu_names
from .whole_pizzas import whole_pizza_summary
from .pmix import product_mix
from .clover_history import pizza_size
from .pmix import product
from .corner.item_quality import latest_counts

METRICS = {
    'sales': ('toast.net_sales','all','USD net sales'),
    'orders': ('toast.orders','all','orders'),
    'whole_pizzas': (None,'all','whole pizzas'),
    'wing_orders': ('toast.item_portions','wings','portions'),
    'wing_pieces': ('toast.item_pieces','wings','pieces'),
    'finger_orders': ('toast.item_portions','fingers','portions'),
    'finger_pieces': ('toast.item_pieces','fingers','pieces'),
    'labor_hours': ('toast.labor_hours','all','recorded hours'),
    'channel_sales': ('toast.channel_sales',None,'USD net sales'),
}
MAX_LIVE_DAYS = 7
MAX_LIVE_PAGES = 20


def requested_dates(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last-first).days > 92:
        raise ValueError('Select 1–93 business dates per lookup.')
    return [first+timedelta(days=i) for i in range((last-first).days+1)]


def clover_values(db, days, metric, dimension=''):
    first,last=days[0].isoformat(),days[-1].isoformat()
    covered=db.rows('SELECT * FROM clover_days WHERE day>=? AND day<=? ORDER BY day',(first,last))
    if metric in {'labor_hours','channel_sales'}:return []
    values={r['day']:{'date':r['day'],'value':r['gross_line_cents']/100 if metric=='sales' else r['paid_orders'],
        'fresh_at':r['imported_at'],'quality':'imported','unlinked_custom_lines':r['custom_lines']} for r in covered}
    if metric not in {'sales','orders'}:
        for row in values.values():row['value']=0;row['unknown_size_portions']=0
        for row in db.rows('SELECT day,name,quantity FROM clover_item_days WHERE day>=? AND day<=?',(first,last)):
            if row['day'] not in values:continue
            target=values[row['day']];qty=row['quantity']
            if metric=='whole_pizzas':
                if pizza_size(row['name']) and (not dimension or pizza_size(row['name'])==dimension):target['value']+=qty
            else:
                category,size=product(row['name'])
                if category!=('wings' if metric.startswith('wing') else 'fingers'):continue
                if metric.endswith('pieces'):
                    if size is None:target['unknown_size_portions']+=qty
                    else:target['value']+=qty*size
                else:target['value']+=qty
        for row in values.values():
            if row['unknown_size_portions']:
                row['observed_known_pieces']=row['value'];row['value']=None;row['quality']='unknown_piece_sizes'
    return list(values.values())


def stored_toast(corner, days, metric, dimension, closeout):
    if not corner:return []
    first,last=days[0].isoformat(),days[-1].isoformat()
    if metric=='whole_pizzas':
        return [{'date':r['day'],'value':(json.loads(r['by_size'])[dimension] if dimension else r['quantity']) if r['quality']=='complete' else None,
                 'quality':r['quality'],'fresh_at':r['last_seen_at'],'retrieval':'stored verified pizza snapshot'}
                for r in latest_counts(corner.store,first,last)]
    key,default,_=METRICS[metric]
    rows=corner.store.rows('''SELECT o.day,o.value,o.quality,d.retrieved_at,d.metadata
        FROM observations o JOIN datasets d ON d.id=o.dataset_id
        WHERE o.metric=? AND o.dimension=? AND o.day>=? AND o.day<=?
        AND o.id=(SELECT MAX(x.id) FROM observations x WHERE x.metric=o.metric
          AND x.dimension=o.dimension AND x.day=o.day) ORDER BY o.day''',
        (key,dimension if metric=='channel_sales' else default,first,last))
    output=[]
    for row in rows:
        try:matching_cutoff=json.loads(row['metadata']).get('closeout_hour')==closeout
        except (ValueError,TypeError):matching_cutoff=False
        complete=row['quality']=='complete' and matching_cutoff
        output.append({'date':row['day'],'value':row['value'] if complete else None,
            'quality':row['quality'] if matching_cutoff else 'closeout_unverified',
            'fresh_at':row['retrieved_at'],'retrieval':'stored Toast aggregate'})
    return output


async def business_lookup(db, toast, corner, metric, start, end, *,dimension='',closeout=4,now=None):
    if metric not in METRICS:raise ValueError('Unsupported aggregate metric.')
    if not isinstance(dimension,str) or (dimension and not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,80}',dimension)):
        raise ValueError('Use a configured nonpersonal channel key.')
    if metric=='channel_sales' and not dimension:raise ValueError('Channel sales require a configured channel key.')
    if metric=='whole_pizzas' and dimension not in {'','Lrg','Med','Mini','GF'}:
        raise ValueError('Use Lrg, Med, Mini, GF, or empty for all whole pizza sizes.')
    if metric not in {'channel_sales','whole_pizzas'} and dimension:raise ValueError('Only channel sales and pizza sizes accept a dimension.')
    days=requested_dates(start,end)
    now=now or datetime.now(timezone.utc);today=business_day(now,closeout)
    if days[-1]>today:
        raise ValueError('Actuals cannot include future dates. Use get_booked_orders for bookings or get_corner predictions for forecasts.')
    sources=[];notes=[]
    clover=clover_values(db,days,metric,dimension)
    toast_rows=stored_toast(corner,days,metric,dimension,closeout)
    by_day={r['date']:r for r in toast_rows if r['date']<today.isoformat()}
    # Fresh reads repair missing/flagged recent observations, never extrapolate.
    missing=[d for d in days if d>=today-timedelta(days=31) and
        (d.isoformat() not in by_day or by_day[d.isoformat()]['value'] is None)]
    pages=0;live_days=0;names=None
    if toast is not None and metric not in {'labor_hours','channel_sales'}:
        try:
            async with asyncio.timeout(60):
                for day in missing[:MAX_LIVE_DAYS]:
                    orders=[];complete=False
                    while pages<MAX_LIVE_PAGES:
                        page=len(orders)//100+1;pages+=1
                        batch=await toast.get('/orders/v2/ordersBulk',{'businessDate':day.strftime('%Y%m%d'),
                            'page':page,'pageSize':100},ttl=120)
                        if not isinstance(batch,list) or len(batch)>100 or any(not isinstance(r,dict) for r in batch):
                            raise ProviderError('Malformed Toast response.')
                        orders.extend(batch)
                        if len(batch)<100:complete=True;break
                    if not complete:
                        notes.append('Live order page budget reached; incomplete dates withheld.');break
                    live_days+=1
                    summary=sales_summary(orders,day,now,closeout)
                    if metric in {'sales','orders'}:
                        value=summary['net_sales' if metric=='sales' else 'orders']
                    else:
                        if names is None:names=menu_names(await toast.get('/menus/v2/menus',ttl=3600))
                        if not names:raise ProviderError('Menu classification unavailable.')
                        if metric=='whole_pizzas':
                            report=whole_pizza_summary(orders,day,now,closeout,group_names=names)
                            value=report['by_size'][dimension] if dimension and report['quantity'] is not None else report['quantity']
                        else:
                            category='wings' if metric.startswith('wing') else 'fingers'
                            report=product_mix(orders,names,now)
                            mix=report['categories'][category]
                            value=mix['total_pieces' if metric.endswith('pieces') else 'portion_orders'] if report['exact'] else None
                    # Empty historical API response is not independent proof of zero.
                    quality='complete' if value is not None and summary['orders'] and day<today else 'partial_or_unconfirmed'
                    by_day[day.isoformat()]={'date':day.isoformat(),'value':value if quality=='complete' else None,
                        'observed_value':value,'quality':quality,'fresh_at':now.isoformat(),'retrieval':'live Toast API'}
        except (ProviderError,TimeoutError,ValueError,TypeError,KeyError,OSError):
            notes.append('Live Toast retrieval unavailable or interrupted; retained stored aggregates and explicit gaps.')
    for name,rows,unit in [('Clover imported history',clover,'USD approximate gross line sales' if metric=='sales' else METRICS[metric][2]),
                           ('Toast',list(by_day.values()),METRICS[metric][2])]:
        rows=sorted(rows,key=lambda r:r['date']);known=[r for r in rows if r['value'] is not None]
        covered={r['date'] for r in known};gaps=[d.isoformat() for d in days if d.isoformat() not in covered]
        sources.append({'source':name,'unit':unit,'daily':rows[-31:],'daily_rows_truncated':len(rows)>31,
            'covered_days':len(covered),'requested_days':len(days),
            'missing_or_unconfirmed_dates':gaps,'complete':not gaps,
            'covered_total':round(sum(r['value'] for r in known),2) if known else None,
            'full_period_total':round(sum(r['value'] for r in known),2) if not gaps else None})
    return {'metric':metric,'dimension':dimension,'start':start,'end':end,'retrieved_at':now.isoformat(),
        'sources':sources,'live_order_page_requests':pages,'live_dates_read':live_days,
        'limits':{'days':93,'live_dates':MAX_LIVE_DAYS,'live_order_pages':MAX_LIVE_PAGES,'seconds':60},
        'recovery_notes':notes,'limitations':[
            'Sources remain separate: Clover gross line sales and Toast net sales are not interchangeable or additive.',
            'Missing, flagged, unconfirmed-zero and current-day values are not completed-day totals. Coverage is not lifetime history.',
            'Stored observations are historical snapshots and may predate corrections. Live repair is bounded to seven recent missing dates per query.',
            'Whole pizzas exclude slices, calzones, package contents and modifiers. Wings/fingers distinguish portions and known pieces.',
            'Toast labor excludes unrecorded Homebase hours; it is not complete restaurant labor. Channel data needs a verified mapping.',
            'This tool retrieves actuals only. Bookings and forecasts are separate; profit requires verified cost data.']}
