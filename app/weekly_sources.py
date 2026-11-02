"""Saturday source refreshes. No AI, browser automation, or external writes."""
import asyncio
from datetime import datetime, timedelta, time
import hashlib
import json
from pathlib import Path
import re

from .calculations import ET
from .monday_export import parse_export
from .storage import ROOT, utcnow


def slot(now):
    local = now.astimezone(ET)
    saturday = local.date()-timedelta(days=(local.weekday()-5) % 7)
    due = datetime.combine(saturday, time(4), ET)
    return due if local >= due else due-timedelta(days=7)


class WeeklySources:
    def __init__(self, db, calendar, monday, directory=None):
        self.db, self.calendar, self.monday = db, calendar, monday
        self.directory = Path(directory) if directory else ROOT/'imports'/'monday'
        self.lock = asyncio.Lock()

    def candidate_signature(self):
        if not self.directory.exists():
            return None
        candidates = sorted((p for p in self.directory.glob('*.xlsx') if p.is_file() and not p.is_symlink()),
                            key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        return hashlib.sha256(json.dumps([(p.name, p.stat().st_size, p.stat().st_mtime_ns)
                                          for p in candidates]).encode()).hexdigest()

    def status(self, now=None):
        now = now or datetime.now(ET)
        config = self.db.get('weekly_sources_config', {})
        due = slot(now)
        state = self.db.get('weekly_sources:'+due.date().isoformat(), {})
        complete = state.get('calendar', {}).get('status') == 'complete' and state.get('monday', {}).get('status') == 'complete'
        eligible = bool(config.get('approved_at') and due >= datetime.fromisoformat(config['approved_at']))
        next_due = due if eligible and not complete else due+timedelta(days=7)
        return {'enabled': config.get('enabled', False), 'schedule': 'Saturday 04:00 America/New_York',
                'approved_at': config.get('approved_at'), 'next_scheduled_at': next_due.isoformat(),
                'current_cycle': state, 'import_directory': str(self.directory),
                'monday_mode': 'Manual Excel export; automatic local import. No automatic board download.',
                'policy': 'Catch up after sleep/restart; preserve prior snapshots on errors. No model calls or automatic messages.'}

    def _import_monday(self, due, now):
        status = self.monday.status()
        if not status.get('board_url'):
            return {'status': 'not_configured'}
        if not self.directory.exists():
            return {'status': 'awaiting_export'}
        candidates = sorted((p for p in self.directory.glob('*.xlsx') if p.is_file() and not p.is_symlink()
                             and p.resolve().parent == self.directory.resolve()), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        previous = self.db.get('monday_weekly_file', {})
        expected = re.sub(r'[^a-z0-9]', '', (status.get('board_name') or '').casefold())
        cutoff = (due-timedelta(days=7)).timestamp()
        errors = []
        for path in candidates:
            stat = path.stat()
            if stat.st_mtime < cutoff or stat.st_mtime > now.timestamp()+300:
                continue
            if not 0 < stat.st_size <= 8_000_000:
                errors.append('FileSizeError')
                continue
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest == previous.get('sha256'):
                return {'status': 'awaiting_export', 'reason': 'Latest export already imported.',
                        'last_imported_at': status.get('imported_at')}
            if status.get('imported_at') and stat.st_mtime < datetime.fromisoformat(status['imported_at']).timestamp():
                continue
            try:
                parsed = parse_export(data, status['board_url'])
                actual = re.sub(r'[^a-z0-9]', '', parsed['board'].casefold())
                if not expected or actual != expected:
                    errors.append('BoardNameMismatch')
                    continue
            except Exception as error:
                errors.append(type(error).__name__)
                continue
            # Parsing and board-name validation finish before the current snapshot changes.
            provenance = {'sha256': digest, 'filename': path.name, 'imported_at': utcnow(),
                          'file_modified_at': datetime.fromtimestamp(stat.st_mtime, ET).isoformat()}
            parsed['export_provenance'] = provenance
            with self.db.connect() as c:
                for key, value in [('monday_snapshot', parsed), ('monday_weekly_file', provenance)]:
                    c.execute('INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                              (key, json.dumps(value)))
            return {'status': 'complete', 'items': len(parsed['items']), **provenance}
        return {'status': 'failed', 'error': ','.join(sorted(set(errors)))} if errors else {'status': 'awaiting_export'}

    async def tick(self, now=None):
        now = now or datetime.now(ET)
        config = self.db.get('weekly_sources_config', {})
        if not config.get('enabled'):
            return {'status': 'disabled'}
        due = slot(now)
        if not config.get('approved_at') or due < datetime.fromisoformat(config['approved_at']):
            return {'status': 'not_due'}
        async with self.lock:
            key = 'weekly_sources:'+due.date().isoformat()
            state = self.db.get(key, {'due_at': due.isoformat()})
            calendar = state.get('calendar', {})
            if calendar.get('status') != 'complete' and calendar.get('attempts', 0) < 3:
                attempts = calendar.get('attempts', 0)+1
                try:
                    result = await asyncio.wait_for(self.calendar.refresh(force=True), timeout=45)
                    calendar = {'status': 'complete' if result.get('configured') and not result.get('last_error') else 'failed',
                                'attempts': attempts, 'checked_at': utcnow(), 'fetched_at': result.get('fetched_at'),
                                'events': result.get('events')}
                except Exception as error:
                    calendar = {'status': 'failed', 'attempts': attempts, 'error': type(error).__name__}
                state['calendar'] = calendar
                self.db.set(key, state)
            board = state.get('monday', {})
            signature = await asyncio.to_thread(self.candidate_signature)
            if board.get('status') == 'failed' and board.get('candidate_signature') != signature:
                board = {}  # A corrected/new export gets its own bounded attempts.
            if board.get('status') != 'complete' and board.get('attempts', 0) < 3:
                try:
                    result = await asyncio.to_thread(self._import_monday, due, now)
                except Exception as error:
                    result = {'status': 'failed', 'error': type(error).__name__}
                result['attempts'] = board.get('attempts', 0)+(result['status'] == 'failed')
                result['candidate_signature'] = signature
                result['checked_at'] = utcnow()
                state['monday'] = result
                self.db.set(key, state)
            return state

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as error:
                self.db.set('weekly_sources_error', {'at': utcnow(), 'error': type(error).__name__})
            await asyncio.sleep(300)
