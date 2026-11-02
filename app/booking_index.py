"""Versioned operational reference index. No customers, checks or order payloads."""
from datetime import date, datetime, timedelta, timezone
import re

UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')


class BookingIndex:
    def __init__(self, db):
        self.db = db
        with db.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS booking_references_v1(
                identity TEXT PRIMARY KEY, due_day TEXT NOT NULL, seen_at TEXT NOT NULL)''')
            c.execute('CREATE INDEX IF NOT EXISTS booking_due_v1 ON booking_references_v1(due_day)')
            c.execute("INSERT OR IGNORE INTO meta VALUES('booking_index_schema','1')")

    def ids(self, day):
        day = date.fromisoformat(str(day)).isoformat()
        return [r['identity'] for r in self.db.rows(
            'SELECT identity FROM booking_references_v1 WHERE due_day=? ORDER BY identity', (day,))]

    def observe(self, orders, closeout, now):
        from .booked_orders import scheduled_day
        today = now.astimezone(timezone.utc).date()
        with self.db.connect() as c:
            c.execute('DELETE FROM booking_references_v1 WHERE due_day<?',
                      ((today-timedelta(days=7)).isoformat(),))
            for row in orders:
                identity = row.get('guid') if isinstance(row, dict) else None
                if not isinstance(identity, str) or not UUID.fullmatch(identity):
                    continue
                try:
                    due = scheduled_day(row, closeout)
                except (ValueError, TypeError, AttributeError):
                    continue
                # Inactive, completed, undated or moved bookings must not retain
                # a stale reference to the former fulfillment date.
                c.execute('DELETE FROM booking_references_v1 WHERE identity=?', (identity,))
                if (due is None or not today-timedelta(days=1) <= due <= today+timedelta(days=366)
                        or any(row.get(k) for k in ('voided','deleted','createdInTestMode','excessFood'))
                        or row.get('approvalStatus') not in {'FUTURE','APPROVED','NEEDS_APPROVAL'}):
                    continue
                c.execute('INSERT INTO booking_references_v1 VALUES(?,?,?)',
                          (identity, due.isoformat(), now.isoformat()))
            # Bounded references, never retained customer data. Eviction cannot
            # establish full booking coverage, which every report disclaims.
            c.execute('''DELETE FROM booking_references_v1 WHERE identity NOT IN
                (SELECT identity FROM booking_references_v1 ORDER BY seen_at DESC,identity LIMIT 5000)''')
