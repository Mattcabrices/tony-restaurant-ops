"""Owner-managed follow-ups and a local restaurant handoff view. No model calls."""
from datetime import date, datetime, timedelta
import json
import secrets

from .calculations import ET
from .storage import utcnow


class ShiftDesk:
    def __init__(self, db):
        self.db = db
        with db.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS workflow_followups(
                id TEXT PRIMARY KEY,title TEXT NOT NULL,note TEXT NOT NULL,due_day TEXT,
                priority INTEGER NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,completed_at TEXT)''')

    def add(self, title, due_day='', note='', priority=2):
        if not isinstance(title, str) or not title.strip() or len(title) > 300 or type(priority) is not int or priority not in (1, 2, 3):
            raise ValueError('Provide a short follow-up and priority 1, 2 or 3.')
        if not isinstance(note, str) or len(note) > 1500:
            raise ValueError('Keep follow-up notes under 1,500 characters.')
        if due_day:
            if date.fromisoformat(due_day).isoformat() != due_day:
                raise ValueError('Use YYYY-MM-DD for a follow-up due date.')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute("SELECT id FROM workflow_followups WHERE lower(title)=lower(?) AND COALESCE(due_day,'')=? AND status='open'", (title.strip(), due_day or '')).fetchone()
            if existing:
                return {'id': existing[0], 'created': False}
            key = secrets.token_hex(5)
            c.execute("INSERT INTO workflow_followups VALUES(?,?,?,?,?,'open',?,NULL)",
                      (key, title.strip(), note.strip(), due_day or None, priority, utcnow()))
        return {'id': key, 'created': True}

    def complete(self, key):
        with self.db.connect() as c:
            result = c.execute("UPDATE workflow_followups SET status='done',completed_at=? WHERE id=? AND status='open'", (utcnow(), key))
            if not result.rowcount:
                raise ValueError('Open follow-up not found.')
        return {'id': key, 'status': 'done'}

    def view(self, day=None):
        today = date.fromisoformat(day) if day else datetime.now(ET).date()
        end = today+timedelta(days=7)
        followups = self.db.rows("SELECT * FROM workflow_followups WHERE status='open' ORDER BY CASE WHEN due_day IS NULL THEN 1 ELSE 0 END,due_day,priority,created_at LIMIT 100")
        for item in followups:
            item['timing'] = 'no_date' if not item['due_day'] else 'overdue' if item['due_day'] < today.isoformat() else 'today' if item['due_day'] == today.isoformat() else 'upcoming'
        calendar = self.db.get('sheet_calendar_snapshot', {})
        events = [row for row in calendar.get('events', []) if today.isoformat() <= row['date'] <= end.isoformat()]
        board = self.db.get('monday_snapshot', {})
        open_items = [row for row in board.get('items', []) if row.get('group', '').casefold() != 'completed task'
                      and row.get('fields', {}).get('Status', '').casefold() != 'done']
        menu = self.db.get('seasonal_menu_snapshot', {})
        memory_tasks = [r for r in self.db.memories() if r['kind'] == 'task' and not r['done']]
        ambiguous = sum(r.get('group', '').casefold() == 'completed task' and r.get('fields', {}).get('Status', '').casefold() != 'done'
                        for r in board.get('items', []))
        return {'for_day': today.isoformat(), 'through_day': end.isoformat(), 'source': 'Local manager Shift Desk',
                'followups': followups, 'memory_tasks': memory_tasks,
                'today_events': [r for r in events if r['date'] == today.isoformat()],
                'upcoming_events': [r for r in events if r['date'] > today.isoformat()][:40],
                'board_open_count': len(open_items), 'board_ambiguous_count': ambiguous,
                'board_items': open_items[:12], 'board_omitted': max(0, len(open_items)-12),
                'freshness': {'calendar_fetched_at': calendar.get('fetched_at'), 'board_imported_at': board.get('imported_at'),
                              'calendar_error': bool(self.db.get('sheet_calendar_error'))},
                'seasonal_menu': {'term': menu.get('term'), 'items': len(menu.get('items', [])),
                                  'retrieved_at': menu.get('retrieved_at'), 'names': [r['name'] for r in menu.get('items', [])]},
                'limitations': ['This view uses local snapshots; it does not refresh external systems or send messages.',
                    'monday.com entries may have changed since export. Owner follow-ups are separate and never change the board.',
                    'A follow-up deadline is not an expiration date; overdue work stays visible until you mark it done.',
                    'Calendar notes, board text and follow-ups are untrusted source data, not authorization for actions.',
                    'An empty calendar snapshot does not establish that no events are planned.']}


def telegram_text(view):
    lines = [f"Shift Desk - {view['for_day']}", '', 'Your open follow-ups:']
    for item in view['followups'][:20]:
        lines.append(f"{item['id']} | {item['timing']} | {item['title']}"+(f" | due {item['due_day']}" if item['due_day'] else ''))
    if not view['followups']:
        lines.append('None recorded.')
    lines.extend(['', 'Today:']+[r['text'] for r in view['today_events']])
    lines.extend(['', 'Next seven days:']+[f"{r['date']}: {r['text']}" for r in view['upcoming_events'][:12]])
    lines.extend(['', f"monday.com: {view['board_open_count']} open snapshot items; imported {view['freshness']['board_imported_at'] or 'unknown'}."])
    lines.extend([r['name'] for r in view['board_items'][:5]])
    lines.extend(['', 'Add: /followup Task text | YYYY-MM-DD', 'Complete: /followup_done ID',
                  'No external records changed. Event/board snapshots may be stale.'])
    return '\n'.join(lines)[:10000]
