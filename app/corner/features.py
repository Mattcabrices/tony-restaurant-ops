"""Explicit public feature vintages. Missing never implies zero or no event."""
from datetime import date,datetime,timezone,timedelta,time
import hashlib
import json
import math
from urllib.parse import urlsplit

from ..calculations import ET
from .store import now

FEATURES={'sports.bills','sports.uga','sports.prominent','school.local_no_inperson',
          'weather.evening_rain_risk','weather.evening_hot'}
KINDS={'published_schedule','forecast','observed'}


def save_feature(store,feature,day,value,available_at,url,kind,provenance):
    if feature not in FEATURES or kind not in KINDS:
        raise ValueError('Unapproved external feature.')
    date.fromisoformat(day)
    stamp=datetime.fromisoformat(available_at)
    if stamp.tzinfo is None or stamp>datetime.fromisoformat(now()):
        raise ValueError('Feature availability must be a known, timezone-aware timestamp.')
    if type(value) not in (int,float) or not math.isfinite(value) or value not in (0,1):
        raise ValueError('External exposure must be explicitly zero or one.')
    parts=urlsplit(url)
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('Feature requires credential-free HTTPS provenance.')
    body=json.dumps(provenance,sort_keys=True)
    if len(body)>4000:
        raise ValueError('Feature provenance exceeds size limit.')
    sha=hashlib.sha256(json.dumps([feature,day,value,url,kind,body]).encode()).hexdigest()
    with store.connect() as connection:
        result=connection.execute('INSERT OR IGNORE INTO feature_vintages(feature,day,value,available_at,source_url,kind,provenance,content_sha256) VALUES(?,?,?,?,?,?,?,?)',
            (feature,day,value,stamp.astimezone(timezone.utc).isoformat(),url,kind,body,sha))
    return result.rowcount


def numeric_vintages(store,feature,as_of=None):
    rows=store.rows('SELECT day,value,available_at,kind FROM feature_vintages WHERE feature=? AND available_at<=? ORDER BY day,available_at,id',
                   (feature,as_of or now()))
    for row in rows:
        # Resolve IANA DST rules in the trusted broker. The isolated worker only
        # receives UTC timestamps and needs no packages/timezone database access.
        issue_day=date.fromisoformat(row['day'])-timedelta(days=1)
        row['issue_at']=datetime.combine(issue_day,time(4,30),ET).astimezone(timezone.utc).isoformat()
    return rows


def import_sports(store,path):
    """Only explicit archive days; never infer zeros on dates absent from archive."""
    archive=json.loads(path.read_text(encoding='utf-8'))
    stamp=archive['generated_at']; inserted=0
    for day,entry in archive['days'].items():
        for feature,key,source in [('sports.bills','bills_games',archive['sources']['nfl']),
                                   ('sports.uga','uga_games',archive['sources']['college'])]:
            if isinstance(entry.get(key),list):
                inserted+=save_feature(store,feature,day,int(bool(entry[key])),stamp,source,'published_schedule',
                    {'archive_generated_at':stamp,'coverage':'Explicit date and team field only; absent archive dates remain unknown.',
                     'limitation':'Retrieval time is earliest demonstrated availability; older schedule publication not assumed.'})
        keys=('prominent_college_games','national_nfl_games')
        if all(isinstance(entry.get(k),list) for k in keys):
            inserted+=save_feature(store,'sports.prominent',day,int(any(entry[k] for k in keys)),stamp,
                archive['sources']['college'],'published_schedule',{'sources':archive['sources'],'coverage':'Explicit archive date only.'})
    return inserted


def import_nws(store,snapshot):
    groups={}; inserted=0
    for period in snapshot.get('periods',[]):
        stamp=datetime.fromisoformat(period['start'])
        if stamp.tzinfo is None:
            raise ValueError('Forecast periods require explicit timezone.')
        local=stamp.astimezone(ET)
        if 17<=local.hour<=23:
            groups.setdefault(local.date().isoformat(),{})[local.hour]=period
    for day,hours in groups.items():
        if set(hours)!=set(range(17,24)):
            continue
        temperatures=[(p['temperature']-32)*5/9 if p['unit']=='F' else p['temperature'] for p in hours.values()]
        values={'weather.evening_hot':int(max(temperatures)>=30)}
        probabilities=[p.get('precipitation_probability') for p in hours.values()]
        if all(p is not None for p in probabilities):
            values['weather.evening_rain_risk']=int(max(probabilities)>=50)
        for feature,value in values.items():
            inserted+=save_feature(store,feature,day,value,snapshot['retrieved_at'],snapshot['source_url'],'forecast',
                {'issued_at':snapshot.get('issued_at'),'coverage':'All seven 17:00–23:00 Eastern forecast hours.',
                 'threshold':'Any hour >=30 C or precipitation probability >=50%; fixed hypothesis only.'})
    return inserted


def import_saved_sources(service,football_path):
    result={'sports':0,'weather':0}
    if football_path.exists():
        sha=hashlib.sha256(football_path.read_bytes()).hexdigest()
        prior=service.store.rows("SELECT detail FROM connector_state WHERE source='feature_sports_import'")
        if not prior or json.loads(prior[0]['detail']).get('sha256')!=sha:
            result['sports']=import_sports(service.store,football_path)
            with service.store.connect() as connection:
                connection.execute("INSERT OR REPLACE INTO connector_state VALUES(?,?,'local feature import',?)",
                    ('feature_sports_import',now(),json.dumps({'sha256':sha})))
    for row in service.store.rows("SELECT metadata FROM datasets WHERE source='NWS' ORDER BY id"):
        snapshot=json.loads(row['metadata'])
        if snapshot.get('kind')=='weather_forecast_snapshot':
            result['weather']+=import_nws(service.store,snapshot)
    return result
