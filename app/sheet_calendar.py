"""Read-only import of the the restaurant monthly Google Sheet calendar."""

import asyncio
import io
import re
import zipfile
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from .calculations import ET as EASTERN
from .storage import utcnow


MONTHS = ('January', 'February', 'March', 'April', 'May', 'June',
          'July', 'August', 'September', 'October', 'November', 'December')
MAIN = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
SHEET_ID = re.compile(r'^/spreadsheets/d/([A-Za-z0-9_-]{20,100})(?:/|$)')
CELL = re.compile(r'^([A-Z]+)([0-9]+)$')


def sheet_id(link):
    parsed = urlparse(link.strip())
    match = SHEET_ID.match(parsed.path)
    if parsed.scheme != 'https' or parsed.hostname != 'docs.google.com' or not match:
        raise ValueError('Use a Google Sheets sharing link from docs.google.com.')
    return match.group(1)


def _cells(root, strings):
    result = {}
    for cell in root.iter(MAIN + 'c'):
        address = cell.attrib.get('r', '')
        if not CELL.fullmatch(address):
            continue
        kind = cell.attrib.get('t')
        value = cell.find(MAIN + 'v')
        if kind == 'inlineStr':
            raw = ''.join(t.text or '' for t in cell.iter(MAIN + 't'))
        elif value is None:
            continue
        elif kind == 's':
            raw = strings[int(value.text)]
        else:
            raw = value.text or ''
        result[address] = raw
    return result


def parse_workbook(data):
    if len(data) > 8_000_000:
        raise ValueError('Calendar workbook is too large.')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 25_000_000:
            raise ValueError('Calendar workbook expands beyond the size limit.')
        book = ET.fromstring(archive.read('xl/workbook.xml'))
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {r.attrib['Id']: r.attrib['Target'].lstrip('/') for r in rels.iter(PKG_REL + 'Relationship')}
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            strings = [''.join(t.text or '' for t in entry.iter(MAIN + 't')) for entry in shared.iter(MAIN + 'si')]
        sheets = {}
        for sheet in book.iter(MAIN + 'sheet'):
            name = sheet.attrib['name']
            if name not in MONTHS and name != 'Year':
                continue
            target = targets[sheet.attrib[REL + 'id']]
            path = target if target.startswith('xl/') else 'xl/' + target
            sheets[name] = _cells(ET.fromstring(archive.read(path)), strings)
    try:
        year = int(float(sheets['Year']['Z1']))
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError('The Year tab must contain a four-digit year in Z1.') from error
    if not 2020 <= year <= 2100:
        raise ValueError('Calendar year is outside the supported range.')
    if sum(name in sheets for name in MONTHS) < 12:
        raise ValueError('Expected twelve monthly calendar tabs.')
    events = []
    for month, name in enumerate(MONTHS, 1):
        cells = sheets[name]
        for row in range(4, 16, 2):
            for column in 'BCDEFGH':
                raw_day = cells.get(f'{column}{row}', '')
                try:
                    day = int(float(raw_day))
                    if float(raw_day) != day:
                        continue
                    event_date = date(year, month, day)
                except (ValueError, TypeError):
                    continue
                note = ' '.join(cells.get(f'{column}{row+1}', '').split())[:500]
                if note:
                    events.append({'date': event_date.isoformat(), 'text': note, 'sheet': name,
                                   'cell': f'{column}{row+1}'})
    if len(events) < 10:
        raise ValueError('No substantial event notes were found in the monthly tabs.')
    return {'year': year, 'events': events}


class SheetCalendar:
    def __init__(self, vault, db):
        self.vault, self.db = vault, db
        self.lock = asyncio.Lock()

    def id(self):
        return self.vault.read().get('sheet_calendar', {}).get('id')

    def status(self):
        snapshot = self.db.get('sheet_calendar_snapshot') or {}
        return {'configured': bool(self.id()), 'year': snapshot.get('year'),
                'events': len(snapshot.get('events', [])), 'fetched_at': snapshot.get('fetched_at'),
                'last_error': self.db.get('sheet_calendar_error'),
                'weekly_refresh_schedule': 'Saturday 04:00 America/New_York' if self.db.get('weekly_sources_config', {}).get('enabled') else None}

    async def _fetch(self, identifier):
        url = f'https://docs.google.com/spreadsheets/d/{identifier}/export?format=xlsx'
        async with httpx.AsyncClient(trust_env=False, timeout=30, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return parse_workbook(response.content)

    async def connect(self, link):
        identifier = sheet_id(link)
        snapshot = await self._fetch(identifier)
        snapshot.update(fetched_at=utcnow(), source_id=identifier)
        self.vault.update(sheet_calendar={'id': identifier})
        self.db.set('sheet_calendar_snapshot', snapshot)
        self.db.set('sheet_calendar_error', None)
        return self.status()

    async def refresh(self, force=False):
        identifier = self.id()
        if not identifier:
            return self.status()
        async with self.lock:
            snapshot = self.db.get('sheet_calendar_snapshot') or {}
            fetched = snapshot.get('fetched_at')
            if not force and fetched:
                try:
                    if datetime.now(EASTERN) - datetime.fromisoformat(fetched) < timedelta(hours=6):
                        return self.status()
                except ValueError:
                    pass
            last_attempt = self.db.get('sheet_calendar_last_attempt')
            if not force and self.db.get('sheet_calendar_error') and last_attempt:
                try:
                    if datetime.now(EASTERN) - datetime.fromisoformat(last_attempt) < timedelta(hours=1):
                        return self.status()
                except ValueError:
                    pass
            self.db.set('sheet_calendar_last_attempt', utcnow())
            try:
                fresh = await self._fetch(identifier)
                fresh.update(fetched_at=utcnow(), source_id=identifier)
                self.db.set('sheet_calendar_snapshot', fresh)
                self.db.set('sheet_calendar_error', None)
            except (httpx.HTTPError, ValueError, zipfile.BadZipFile, ET.ParseError, KeyError, IndexError) as error:
                self.db.set('sheet_calendar_error', type(error).__name__ + ': ' + str(error)[:180])
            return self.status()

    async def events(self, start, end, limit=60):
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if last < first or (last-first).days > 366:
            raise ValueError('Calendar range must be chronological and at most 367 days.')
        await self.refresh()
        snapshot = self.db.get('sheet_calendar_snapshot') or {}
        matches = [entry for entry in snapshot.get('events', []) if start <= entry['date'] <= end]
        return {'source': 'the restaurant shared Google Sheet (read-only monthly tabs)',
                'source_url': f'https://docs.google.com/spreadsheets/d/{self.id()}/edit' if self.id() else None,
                'fetched_at': snapshot.get('fetched_at'), 'year': snapshot.get('year'),
                'weekly_refresh_schedule': self.status()['weekly_refresh_schedule'],
                'range': {'start': start, 'end': end}, 'events': matches[:limit],
                'omitted': max(0, len(matches)-limit), 'error': self.db.get('sheet_calendar_error'),
                'limitations': ['Event notes are entered by staff and may be tentative or outdated.',
                                'Times are copied as written; they are not independently verified.',
                                'This calendar is separate from official sports schedules and POS sales.']}
