"""Nonpersonal item-count snapshots and coverage warnings, separate from forecasts."""
from datetime import date,timedelta,datetime,timezone
import hashlib
import json
import math

from .store import now


def save_whole_pizzas(store,report,retrieved_at=None):
    date.fromisoformat(report['business_date'])
    quantity=report['quantity'];quality=report['quality']
    if quality not in {'complete','partial'} or (quality=='complete' and quantity is None):
        raise ValueError('Invalid whole-pizza snapshot quality.')
    values=report['by_size']
    if set(values)!={'Lrg','Med','Mini','GF'} or any(type(x) not in (float,int) or not math.isfinite(x) or x<0 or x!=int(x) for x in values.values()):
        raise ValueError('Whole-pizza size counts must be nonnegative whole units.')
    if report['observed_quantity']!=sum(values.values()) or (quality=='complete' and quantity!=report['observed_quantity']) or (quality=='partial' and quantity is not None):
        raise ValueError('Whole-pizza snapshot totals are inconsistent.')
    body={k:report[k] for k in ('quantity','observed_quantity','by_size','quality','issues')}
    body['calculation_version']=report.get('calculation_version','whole-pizza-v1')
    body['reconciliation']=report.get('reconciliation',{})
    sha=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest();stamp=retrieved_at or now()
    parsed=datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:raise ValueError('Snapshot retrieval requires a timezone.')
    stamp=parsed.astimezone(timezone.utc).isoformat()
    with store.connect() as c:
        c.execute('''INSERT INTO item_count_snapshots(source,day,category,mapping_version,mapping_sha256,quantity,
          observed_quantity,by_size,quality,issues,content_sha256,first_seen_at,last_seen_at,calculation_version,reconciliation)
          VALUES('Toast',?,'whole_pizza',?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(source,day,category,mapping_sha256,content_sha256) DO UPDATE SET last_seen_at=excluded.last_seen_at''',
          (report['business_date'],report['mapping_version'],report['mapping_sha256'],quantity,report['observed_quantity'],
           json.dumps(report['by_size']),quality,json.dumps(report['issues']),sha,stamp,stamp,body['calculation_version'],json.dumps(body['reconciliation'])))


def latest_counts(store,start='2000-01-01',end='9999-12-31'):
    return store.rows('''SELECT a.* FROM item_count_snapshots a WHERE source='Toast' AND category='whole_pizza'
        AND day>=? AND day<=? AND id=(SELECT b.id FROM item_count_snapshots b WHERE b.source=a.source
        AND b.category=a.category AND b.day=a.day ORDER BY b.last_seen_at DESC,b.id DESC LIMIT 1) ORDER BY day''',(start,end))


def whole_pizza_quality(store,start,end):
    first,last=date.fromisoformat(start),date.fromisoformat(end)
    rows=latest_counts(store,start,end);present={r['day'] for r in rows}
    missing=[];current=first
    while current<=last:
        if current.isoformat() not in present:missing.append(current.isoformat())
        current+=timedelta(days=1)
    partial=[{'day':r['day'],'issues':json.loads(r['issues'])} for r in rows if r['quality']!='complete']
    retrieved=max((r['last_seen_at'] for r in rows),default=None)
    return {'source':'Toast verified whole-pizza snapshots','requested_range':[start,end],
        'first_counted_day':rows[0]['day'] if rows else None,'last_counted_day':rows[-1]['day'] if rows else None,
        'complete_count_days':sum(r['quality']=='complete' for r in rows),'partial_count_days':len(partial),
        'reconciled_split_days':sum(json.loads(r['reconciliation']).get('groups',0)>0 for r in rows),
        'partial_details':partial[:14],'missing_count_days':len(missing),'missing_date_sample':missing[:14],
        'latest_retrieval':retrieved,
        'retrieval_older_than_48_hours':bool(retrieved and (datetime.now(timezone.utc)-datetime.fromisoformat(retrieved)).total_seconds()>172800),
        'limitations':'Missing or flagged days are not zero. Complete means this counting method found no ambiguity, not independent POS reconciliation. Current menu mapping and ordered/refund conventions apply. No extrapolated lifetime record.'}
