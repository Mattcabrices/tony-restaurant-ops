"""Read-only scheduled-order aggregates; no customer records leave this module."""
import asyncio
from collections import Counter
from datetime import date,datetime,timedelta,timezone
import time
import re

from .calculations import business_day,parsed
from .item_sales import menu_names
from .providers import ProviderError
from .whole_pizzas import booked_whole_pizza_summary

MAX_PAGES=40
LOOKBACK_DAYS=7
MAX_KNOWN_ORDERS=20
ORDER_ID=re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')


def known_booking_ids(db,day,closeout):
    """Only trusted local Toast snapshot references; never model-supplied IDs."""
    day=date.fromisoformat(day) if isinstance(day,str) else day
    found=set()
    for key in ('briefing_baseline:live','checkin:live:telegram','checkin:live:web'):
        snapshot=db.get(key,{})
        if not isinstance(snapshot,dict) or not isinstance(snapshot.get('upcoming',[]),list):continue
        for row in snapshot.get('upcoming',[]):
            try:due=business_day(parsed(row.get('when')),closeout)
            except (ValueError,TypeError,AttributeError):continue
            identity=row.get('id')
            if due==day and isinstance(identity,str) and ORDER_ID.fullmatch(identity):found.add(identity)
    return sorted(found)


def scheduled_day(order,closeout):
    stamp=order.get('promisedDate') or order.get('estimatedFulfillmentDate')
    if not stamp and order.get('approvalStatus')=='FUTURE':stamp=order.get('openedDate')
    if stamp:return business_day(parsed(stamp),closeout)
    # A known future business date is usable when no timestamp is supplied.
    value=str(order.get('businessDate') or '')
    return datetime.strptime(value,'%Y%m%d').date() if len(value)==8 else None


def summarize_bookings(orders,day,now,closeout,*,mapping=None,group_names=None):
    eligible=[];seen=set();issues=Counter();pending=0;excluded=0
    for order in orders:
        if not isinstance(order,dict):issues['invalid_order']+=1;continue
        if any(order.get(k) for k in ('voided','deleted','excessFood','createdInTestMode')):
            excluded+=1;continue
        try:due=scheduled_day(order,closeout)
        except (ValueError,TypeError,AttributeError):issues['invalid_fulfillment_date']+=1;continue
        if due is None:
            if order.get('approvalStatus')=='FUTURE':issues['undated_future_order']+=1
            continue
        if due!=day:continue
        identity=order.get('guid')
        if not isinstance(identity,str) or not identity:issues['missing_order_identity']+=1;continue
        if identity in seen:continue
        seen.add(identity)
        status=order.get('approvalStatus')
        if status=='NEEDS_APPROVAL':pending+=1;continue
        if status=='NOT_APPROVED':excluded+=1;continue
        if status not in ('FUTURE','APPROVED'):
            issues['unknown_approval_status']+=1;continue
        checks=order.get('checks')
        if not isinstance(checks,list) or any(not isinstance(c,dict) for c in checks):
            issues['invalid_checks']+=1;continue
        if not any(not c.get('voided') and not c.get('deleted') for c in checks):
            excluded+=1;continue
        eligible.append(order)
    pizzas=booked_whole_pizza_summary(eligible,day,now,closeout,mapping=mapping,group_names=group_names)
    issues.update(pizzas['issues'])
    return {'orders_booked':len(eligible),'pending_approval_orders':pending,
            'whole_pizzas_booked':pizzas['quantity'] if not issues else None,
            'observed_whole_pizzas':pizzas['observed_quantity'],'by_size':pizzas['by_size'],
            'quality_issues':dict(issues),
            'classification_version':pizzas['calculation_version'],
            'mapping_version':pizzas['mapping_version'],'split_reconciliation':pizzas['reconciliation']}


async def booked_order_report(toast,day,closeout=4,*,now=None,known_ids=(),index=None):
    now=now or datetime.now(timezone.utc)
    day=date.fromisoformat(day) if isinstance(day,str) else day
    today=business_day(now,closeout)
    if not isinstance(day,date) or not today<=day<=today+timedelta(days=366):
        raise ProviderError('Booked-order dates must be today through the next 366 business dates.')
    if index is not None:known_ids=(*known_ids,*index.ids(day))
    known_ids=sorted(set(v for v in known_ids if isinstance(v,str) and ORDER_ID.fullmatch(v)))
    key='booked-orders-v2:'+day.isoformat()+':'+str(len(known_ids))+':'+','.join(known_ids[:MAX_KNOWN_ORDERS])
    cached=getattr(toast,'cache',{}).get(key)
    if cached and time.time()-cached[0]<120:return cached[1]
    start=now-timedelta(days=LOOKBACK_DAYS)
    found={};complete={'business_date':False,'recent_modified':False};issues=Counter();pages=0
    names={};known_reads=0
    if len(known_ids)>MAX_KNOWN_ORDERS:issues['known_bookings_truncated']+=1
    try:
        async with asyncio.timeout(90):
            try:names=menu_names(await toast.get('/menus/v2/menus',ttl=3600))
            except (ProviderError,ValueError,TypeError):names={}
            queries=[('business_date',{'businessDate':day.strftime('%Y%m%d')}),
                     ('recent_modified',{'startDate':start.isoformat(timespec='milliseconds'),
                                         'endDate':now.isoformat(timespec='milliseconds')})]
            for phase,params in queries:
                for page in range(1,MAX_PAGES+1):
                    if pages>=MAX_PAGES:break
                    pages+=1
                    try:
                        batch=await toast.get('/orders/v2/ordersBulk',params|{'page':page,'pageSize':100},ttl=120)
                        if not isinstance(batch,list) or len(batch)>100:raise ProviderError('Invalid booked-order response.')
                    except (ProviderError,ValueError,TypeError):
                        # Preserve independent recovery paths inside the same
                        # overall page/time budget, without retrying this phase.
                        issues['scan_phase_unavailable']+=1;break
                    if index is not None:index.observe(batch,closeout,now)
                    for order in batch:
                        if not isinstance(order,dict):issues['invalid_order']+=1;continue
                        # Retain only relevant scheduled rows, not the historical
                        # customer data returned alongside them. Later modified
                        # rows replace stale duplicates from the first query.
                        try:due=scheduled_day(order,closeout)
                        except (ValueError,TypeError,AttributeError):
                            issues['invalid_fulfillment_date']+=1;continue
                        identity=order.get('guid')
                        if phase=='recent_modified' and due!=day and identity in found:
                            found.pop(identity)  # Rescheduled away from the requested day.
                        if due==day or (due is None and order.get('approvalStatus')=='FUTURE'):
                            if isinstance(identity,str) and identity:found[identity]=order
                            else:issues['missing_order_identity']+=1
                    if len(batch)<100:complete[phase]=True;break
            for identity in known_ids[:MAX_KNOWN_ORDERS]:
                known_reads+=1
                # A fresh bulk result must not be overwritten by an older
                # per-order cache entry when the known-reference set changes.
                try:order=await toast.get('/orders/v2/orders/'+identity,ttl=0)
                except ProviderError:
                    issues['known_booking_unavailable']+=1;continue
                if not isinstance(order,dict) or order.get('guid')!=identity:
                    issues['invalid_known_booking']+=1;continue
                if index is not None:index.observe([order],closeout,now)
                try:due=scheduled_day(order,closeout)
                except (ValueError,TypeError,AttributeError):
                    issues['invalid_fulfillment_date']+=1;continue
                if due==day:found[identity]=order
                else:found.pop(identity,None)
    except (ProviderError,TimeoutError,ValueError,TypeError,KeyError):
        issues['retrieval_interrupted']+=1
    if not all(complete.values()):issues['retrieval_incomplete']+=1
    if not names:issues['menu_classification_unavailable']+=1
    try:result=summarize_bookings(list(found.values()),day,now,closeout,group_names=names)
    except (ValueError,KeyError,TypeError,OSError):
        raise ProviderError('Booked pizza classification unavailable; no count was invented.') from None
    issues.update(result['quality_issues'])
    result.update({'source':'Toast Orders API - scheduled bookings, not completed sales or forecast',
        'target_business_date':day.isoformat(),'fresh_at':now.isoformat(),'cache_max_age_seconds':120,
        'scan':{'business_date_complete':complete['business_date'],
                'recent_modified_complete':complete['recent_modified'],
                'modified_since':start.isoformat(),'modified_before':now.isoformat(),
                'orders_api_page_requests':pages,'max_page_requests':MAX_PAGES,
                'known_order_reads':known_reads,'max_known_order_reads':MAX_KNOWN_ORDERS},
        'quality_issues':dict(issues),'scan_complete':all(complete.values()),'exact_within_scan':all(complete.values()) and not issues,
        'limitations':['Current booked snapshot, not a sales forecast, completed sales or proof of payment.',
            'Combines the target-business-date query with orders modified in the past seven days and individually refreshed older booking references from local Toast snapshots, filtered by fulfillment business date.',
            'The persistent reference index remembers previously observed bookings, not undiscovered ones. Older unchanged bookings absent from queries, snapshots and the bounded index may still be missing; counts are not a guaranteed full booking ledger.',
            'Voided/deleted/test/excess-food orders, rejected orders, inactive checks/selections, packages, slices, calzones and modifiers are excluded; pending approval orders are reported separately.',
            'Whole pizzas use the existing explicit identity mapping and verified linked-split reconciliation. Unmapped or ambiguous items withhold exact totals; observed counts are not exact totals.']})
    if issues:result['whole_pizzas_booked']=None
    uncertain_orders={'invalid_order','invalid_fulfillment_date','undated_future_order','missing_order_identity','unknown_approval_status','invalid_checks','known_bookings_truncated','known_booking_unavailable','invalid_known_booking'}
    if not all(complete.values()) or any(k in issues for k in uncertain_orders):
        result['observed_orders_booked']=result['orders_booked'];result['orders_booked']=None
    if hasattr(toast,'cache') and all(complete.values()):toast.cache[key]=(time.time(),result)
    return result
