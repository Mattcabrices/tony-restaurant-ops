"""Source-attributed public data adapters. No web-search or arbitrary URL tools."""
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import ipaddress
import json
import math
from urllib.parse import urlsplit

import httpx

from .store import clean, now


def public_url(url, allowed_hosts):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in allowed_hosts or parts.username or parts.password
            or parts.port not in (None, 443) or parts.fragment):
        raise ValueError('Source URL must use HTTPS on an explicitly approved hostname.')
    if parts.hostname in {'localhost', 'localhost.localdomain'} or parts.hostname.endswith('.local'):
        raise ValueError('Local network research targets are not allowed.')
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError('Private/reserved network research targets are not allowed.')
    return url


async def fetch_public(url, hosts, budget, params=None):
    public_url(url, hosts)
    async with httpx.AsyncClient(trust_env=False, timeout=15, follow_redirects=False, event_hooks={'request': [budget.before_request]}) as client:
        async with client.stream('GET', url, params=params, headers={'User-Agent': 'TonyRestaurant-Tonys-Corner/1.0', 'Accept': 'application/geo+json, application/json, text/calendar, text/csv'}) as response:
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 2_000_000:
                    raise ValueError('Research connector response exceeded size limit.')
    return bytes(data)


def save_events(store, source, url, events, retrieved_at=None):
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('Event provenance must use an HTTPS source without embedded credentials.')
    timestamp = datetime.fromisoformat(retrieved_at or now())
    if timestamp.tzinfo is None:
        raise ValueError('Event retrieval timestamps require a timezone.')
    retrieved_at = timestamp.astimezone(timezone.utc).isoformat()
    with store.connect() as connection:
        for entry in events:
            date.fromisoformat(entry['day'])
            if entry['category'] not in {'school', 'holiday', 'local', 'business', 'sports'}:
                raise ValueError('Unknown event category.')
            label = clean(entry['label'], 300)
            sha = hashlib.sha256(json.dumps([label, entry['category'], url]).encode()).hexdigest()
            connection.execute('INSERT OR IGNORE INTO events(source,source_url,day,label,category,retrieved_at,content_sha256) VALUES(?,?,?,?,?,?,?)',
                (clean(source, 100), url, entry['day'], label, entry['category'], retrieved_at, sha))
        connection.execute('''INSERT INTO connector_state VALUES(?,?,'snapshot',?) ON CONFLICT(source) DO UPDATE SET
            checked_at=excluded.checked_at,status=excluded.status,detail=excluded.detail WHERE excluded.checked_at>=connector_state.checked_at''',
            (clean(source, 100), retrieved_at, json.dumps({'source_url': url, 'events': len(events)})))
    return len(events)


def verified_calendar_csv(store, data, source, url):
    """Explicitly imported dated rows from an official calendar; no generated dates."""
    entries = list(csv.DictReader(io.StringIO(data)))
    if not entries or len(entries) > 2000 or set(entries[0]) != {'day', 'label', 'category'}:
        raise ValueError('Calendar CSV requires day,label,category headers and 1–2000 rows.')
    return save_events(store, source, url, entries)


def calendar_ics(data, category):
    """Bounded all-day event parser; unsupported recurrence is explicitly reported."""
    lines = data.replace('\r\n ', '').replace('\r\n\t', '').splitlines()
    entries, skipped, current = [], [], None
    for line in lines:
        if line == 'BEGIN:VEVENT':
            current = {}
        elif line == 'END:VEVENT' and current is not None:
            if 'RRULE' in current or 'RECURRENCE-ID' in current or 'EXDATE' in current:
                skipped.append('Recurring event requires reviewed expansion/import.')
            elif current.get('STATUS') != 'CANCELLED':
                start, end = current.get('DTSTART', ''), current.get('DTEND', '')
                if len(start) != 8 or (end and len(end) != 8):
                    skipped.append('Timed/TZ-specific event requires a supported adapter; not guessed.')
                else:
                    first = datetime.strptime(start, '%Y%m%d').date()
                    last = datetime.strptime(end, '%Y%m%d').date() if end else first + timedelta(days=1)
                    if not 0 < (last-first).days <= 62:
                        raise ValueError('Calendar event span is invalid or exceeds 62 days.')
                    entries.extend({'day': (first+timedelta(days=n)).isoformat(), 'label': current.get('SUMMARY', 'Calendar event'),
                                    'category': category} for n in range((last-first).days))
            current = None
        elif current is not None and ':' in line:
            key, value = line.split(':', 1)
            current[key.split(';', 1)[0]] = value
        if len(entries) > 2000:
            raise ValueError('Calendar exceeds event limit.')
    return entries, skipped


async def nws_forecast(latitude, longitude, budget):
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError('Invalid restaurant coordinates.')
    # NWS canonicalizes points to four decimals; use that URL directly rather
    # than broadening the connector's no-redirect policy.
    points = json.loads(await fetch_public(f'https://api.weather.gov/points/{latitude:.4f},{longitude:.4f}', {'api.weather.gov'}, budget))
    url = points['properties']['forecastHourly']
    hourly = json.loads(await fetch_public(url, {'api.weather.gov'}, budget))
    periods = hourly['properties']['periods']
    checked = []
    for period in periods[:168]:
        stamp = datetime.fromisoformat(period['startTime'])
        temperature = period['temperature']
        probability = (period.get('probabilityOfPrecipitation') or {}).get('value')
        if (stamp.tzinfo is None or isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not math.isfinite(temperature) or period['temperatureUnit'] not in {'C', 'F'}):
            raise ValueError('NWS returned an invalid temperature/time field.')
        if probability is not None and (isinstance(probability, bool) or not isinstance(probability, (float, int)) or not 0 <= probability <= 100):
            raise ValueError('NWS returned an invalid precipitation probability.')
        checked.append({'start': period['startTime'], 'temperature': temperature, 'unit': period['temperatureUnit'],
                        'precipitation_probability': probability})
    # Preserve issue time and valid time; these forecasts are contextual until tested.
    return {'source_url': url, 'retrieved_at': now(), 'issued_at': hourly['properties'].get('updateTime'),
            'periods': checked, 'trust': 'structured public forecast; not instructions'}


async def noaa_daily(station, start, end, budget):
    import re
    if not re.fullmatch(r'[A-Z0-9]{6,20}', station):
        raise ValueError('A verified NOAA station ID is required.')
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last-first).days > 365:
        raise ValueError('NOAA history requests are limited to 366 days.')
    url = 'https://www.ncei.noaa.gov/access/services/data/v1'
    data = json.loads(await fetch_public(url, {'www.ncei.noaa.gov'}, budget, params={
        'dataset': 'daily-summaries', 'stations': station, 'startDate': start, 'endDate': end,
        'dataTypes': 'TAVG,TMAX,TMIN,PRCP', 'units': 'metric', 'format': 'json','includeAttributes':'true'}))
    rows,quality = noaa_records(data,station,start,end)
    return rows, {'station': station, 'source_url': url, 'requested_start':start,'requested_end':end,
        'units':'metric requested; no additional scaling applied','quality':quality,
        'definition': 'Historical observed weather; never historical forecast vintages. No missing values imputed.'}


def noaa_records(data,station,start,end):
    """Conservative QC: flag/sentinel omissions remain missing, never zero."""
    if not isinstance(data,list) or len(data)>366:
        raise ValueError('Unexpected NOAA daily-summary response.')
    fields={'TAVG':'weather.temperature_c','TMAX':'weather.temperature_max_c',
            'TMIN':'weather.temperature_min_c','PRCP':'weather.precipitation_mm'}
    seen=set(); rows=[]; omitted=[]
    for entry in data:
        day=entry['DATE']; date.fromisoformat(day)
        if not start<=day<=end or day in seen or entry.get('STATION')!=station:
            raise ValueError('NOAA date/station/duplicate validation failed.')
        seen.add(day)
        for field,metric in fields.items():
            raw=entry.get(field)
            if raw in (None,''):
                continue
            attributes=entry.get(field+'_ATTRIBUTES')
            flags=attributes.split(',') if isinstance(attributes,str) else []
            if len(flags)<3:
                raise ValueError('NOAA quality attributes are required for populated observations.')
            if flags[0] or flags[1]:
                omitted.append({'day':day,'field':field,'reason':'measurement/quality flag requires review'})
                continue
            value=float(raw)
            if not math.isfinite(value) or value==-9999 or (field=='PRCP' and value<0):
                omitted.append({'day':day,'field':field,'reason':'missing sentinel or invalid measurement'})
                continue
            if (field=='PRCP' and value>2000) or (field!='PRCP' and not -90<=value<=65):
                raise ValueError('NOAA metric value is outside physical validation bounds.')
            rows.append({'day':day,'metric':metric,'value':value})
    return rows,{'returned_dates':len(seen),'accepted_values':len(rows),'omitted':omitted,
        'coverage':'Only reported, unflagged measurements. Missing/trace/accumulated values not imputed.'}
