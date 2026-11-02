"""Passive local freshness/heartbeat checks. No network, AI, or messages."""
from datetime import datetime, timezone
from .storage import utcnow


def age_hours(stamp, now):
    if not stamp:
        return None
    try:
        value = datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if value.tzinfo is None or value > now:
            return None
        return round((now-value).total_seconds()/3600, 3)
    except (ValueError, TypeError):
        return None


def inspect(db, workers=None, now=None):
    now = now or datetime.now(timezone.utc)
    definitions = [
        ('calendar', 'Staff event calendar', db.get('sheet_calendar_snapshot',{}).get('fetched_at'), 8*24),
        ('monday', 'monday.com manual snapshot', db.get('monday_snapshot',{}).get('imported_at'), 8*24),
        ('menu', 'Seasonal menu catalog snapshot', db.get('seasonal_menu_snapshot',{}).get('retrieved_at'), 14*24),
    ]
    definitions.append(('toast_overview','Toast operating snapshot', db.get('verified_toast_snapshot',{}).get('fresh_at'), 26))
    sources, findings, healthy = [], [], []
    for key, title, stamp, threshold in definitions:
        age = age_hours(stamp, now)
        status = 'not_collected' if not stamp else 'invalid_timestamp' if age is None else 'stale' if age > threshold else 'fresh'
        source = {'key':key,'title':title,'observed_at':stamp,'age_hours':age,'stale_after_hours':threshold,'status':status}
        sources.append(source)
        if status == 'fresh':
            healthy.append('health:source:'+key)
        if status in {'stale','invalid_timestamp'}:
            findings.append({'key':'health:source:'+key,'title':title+' needs a freshness check','source':'source-health',
                             'severity':'warning','evidence':{k:v for k,v in source.items() if k != 'age_hours'}})
    scheduler = db.get('scheduler',{})
    if scheduler.get('last_check'):
        age = age_hours(scheduler['last_check'], now)
        if age is None or age > .25:
            findings.append({'key':'health:worker:scheduler-heartbeat','title':'Scheduler heartbeat is stale',
                'source':'source-health','severity':'warning','evidence':{'last_check':scheduler['last_check'],'stale_after_minutes':15}})
        else:
            healthy.append('health:worker:scheduler-heartbeat')
    for name, state in (workers or {}).items():
        if state != 'Running':
            findings.append({'key':'health:worker:'+name,'title':'Background worker stopped: '+name,
                'source':'source-health','severity':'critical','evidence':{'worker':name,'state':state}})
        else:
            healthy.append('health:worker:'+name)
    return {'source':'Passive local source health','checked_at':now.isoformat(),'sources':sources,'workers':workers or {},
            'findings':findings,'healthy_keys':healthy,'limitations':['Freshness measures retrieval age, not correctness or complete coverage.',
                'Not-collected sources are pending, not evidence of zero activity.',
                'A sleeping or stopped computer cannot run its own checks; no external uptime monitor is configured.',
                'Historical Clover coverage is static; its old import date alone is not an outage.']}
