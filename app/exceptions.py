"""Local operational exception lifecycle; never sends or changes source systems."""
from datetime import datetime, timezone
import hashlib
import json

from .storage import utcnow

SEVERITIES = {'info', 'warning', 'critical'}
STATES = {'open', 'acknowledged', 'resolved', 'false_positive'}


class ExceptionLedger:
    def __init__(self, db):
        self.db = db
        with db.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS exception_schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operational_exceptions(
                  id TEXT PRIMARY KEY,dedupe_key TEXT UNIQUE NOT NULL,title TEXT NOT NULL,
                  source TEXT NOT NULL,severity TEXT NOT NULL,status TEXT NOT NULL,
                  assigned_to TEXT NOT NULL DEFAULT '',evidence TEXT NOT NULL,
                  first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,occurrences INTEGER NOT NULL,
                  due_at TEXT,resolved_at TEXT,revision INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS exception_events(
                  id INTEGER PRIMARY KEY,exception_id TEXT NOT NULL,action TEXT NOT NULL,
                  actor TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL,revision INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS exception_status ON operational_exceptions(status,severity,last_seen);
                INSERT OR IGNORE INTO exception_schema_migrations VALUES(1,strftime('%Y-%m-%dT%H:%M:%fZ','now'));
            ''')

    def observe(self, key, title, source, severity, evidence, due_at=None):
        if not isinstance(key, str) or not 1 <= len(key) <= 200 or severity not in SEVERITIES:
            raise ValueError('Invalid exception identity or severity.')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 300 or not isinstance(source, str) or not 1 <= len(source) <= 100:
            raise ValueError('Invalid exception title or source.')
        encoded = json.dumps(evidence, ensure_ascii=True, allow_nan=False)
        if len(encoded) > 12000:
            raise ValueError('Exception evidence exceeds the bounded local record size.')
        if due_at:
            parsed = datetime.fromisoformat(due_at.replace('Z','+00:00'))
            if parsed.tzinfo is None:
                raise ValueError('Exception due time needs a timezone.')
        stamp, identity = utcnow(), hashlib.sha256(key.encode()).hexdigest()[:20]
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            prior = c.execute('SELECT * FROM operational_exceptions WHERE dedupe_key=?', (key,)).fetchone()
            if prior:
                # False positives remain suppressed; only the manager can reopen.
                status = 'open' if prior['status'] == 'resolved' else prior['status']
                action = 'reopened' if prior['status'] == 'resolved' else 'evidence_updated'
                changed = prior['evidence'] != encoded or prior['severity'] != severity or prior['title'] != title or prior['due_at'] != due_at or status != prior['status']
                revision = prior['revision']+int(changed)
                c.execute('''UPDATE operational_exceptions SET title=?,severity=?,evidence=?,last_seen=?,
                    occurrences=occurrences+1,status=?,due_at=?,revision=?,resolved_at=CASE WHEN ?='open' THEN NULL ELSE resolved_at END WHERE id=?''',
                    (title, severity, encoded, stamp, status, due_at, revision, status, prior['id']))
                identity = prior['id']
                if changed:
                    c.execute('INSERT INTO exception_events VALUES(NULL,?,?,?,?,?,?)',
                        (identity, action, 'deterministic-check', 'Source evidence changed; no external action.', stamp, revision))
            else:
                c.execute('INSERT INTO operational_exceptions VALUES(?,?,?,?,?,\'open\',\'\',?,?,?,?,?,NULL,1)',
                    (identity, key, title.strip(), source, severity, encoded, stamp, stamp, 1, due_at))
                c.execute('INSERT INTO exception_events VALUES(NULL,?,?,?,?,?,1)',
                    (identity, 'observed', 'deterministic-check', 'Recorded locally; no external action.', stamp))
        return self.read(identity)

    def read(self, identity):
        rows = self.db.rows('SELECT * FROM operational_exceptions WHERE id=?', (identity,))
        if not rows:
            raise ValueError('Exception not found.')
        value = rows[0]
        value['evidence'] = json.loads(value['evidence'])
        value['events'] = self.db.rows('SELECT * FROM exception_events WHERE exception_id=? ORDER BY id DESC LIMIT 50', (identity,))
        value['escalation_due'] = bool(value['due_at'] and value['status'] in {'open','acknowledged'} and
            datetime.fromisoformat(value['due_at'].replace('Z','+00:00')) < datetime.now(timezone.utc))
        return value

    def listing(self, include_closed=False):
        where = '' if include_closed else "WHERE status IN ('open','acknowledged')"
        rows = self.db.rows('SELECT id FROM operational_exceptions '+where+" ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,last_seen DESC LIMIT 100")
        counts = self.db.rows('SELECT status,COUNT(*) count FROM operational_exceptions GROUP BY status')
        return {'source': 'Local operational exception ledger', 'retrieved_at': utcnow(),
                'items': [self.read(row['id']) for row in rows], 'counts': {r['status']:r['count'] for r in counts},
                'limitations': ['At most 100 records and 50 recent transitions per record are displayed.',
                    'Escalation due is a local flag; no notifications or third-party messages are sent.',
                    'Acknowledgment and assignment do not change source systems or authorize actions.']}

    def transition(self, identity, status, note, assigned_to='', expected_revision=None):
        if status not in STATES or not isinstance(note, str) or not 1 <= len(note.strip()) <= 1500:
            raise ValueError('Choose an allowed status and provide a short reason.')
        if not isinstance(assigned_to, str) or len(assigned_to) > 120:
            raise ValueError('Assignee must be a short local label.')
        stamp = utcnow()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            prior = c.execute('SELECT * FROM operational_exceptions WHERE id=?', (identity,)).fetchone()
            if not prior:
                raise ValueError('Exception not found.')
            if expected_revision is not None and prior['revision'] != expected_revision:
                raise ValueError('Exception changed; refresh before reviewing.')
            revision = prior['revision']+1
            c.execute('UPDATE operational_exceptions SET status=?,assigned_to=?,resolved_at=?,revision=? WHERE id=?',
                (status, assigned_to.strip(), stamp if status in {'resolved','false_positive'} else None, revision, identity))
            c.execute('INSERT INTO exception_events VALUES(NULL,?,?,?,?,?,?)',
                (identity, status, 'authenticated-manager', note.strip(), stamp, revision))
        return self.read(identity)

    def reconcile(self, findings, healthy_keys=None):
        active = {item['key'] for item in findings}
        for finding in findings:
            self.observe(finding['key'], finding['title'], finding['source'], finding['severity'], finding['evidence'])
        # Only checks in this exact namespace may auto-clear, with explicit history.
        for row in self.db.rows("SELECT id,dedupe_key,revision FROM operational_exceptions WHERE dedupe_key LIKE 'health:%' AND status IN ('open','acknowledged')"):
            if row['dedupe_key'] not in active and (healthy_keys is None or row['dedupe_key'] in healthy_keys):
                self._clear_health(row)

    def _clear_health(self, row):
        with self.db.connect() as c:
            revision, stamp = row['revision']+1, utcnow()
            changed = c.execute("UPDATE operational_exceptions SET status='resolved',resolved_at=?,revision=? WHERE id=? AND revision=?",
                (stamp, revision, row['id'], row['revision']))
            if changed.rowcount:
                c.execute('INSERT INTO exception_events VALUES(NULL,?,?,?,?,?,?)',
                    (row['id'], 'source_recovered', 'deterministic-check', 'Current local health check no longer detects this condition.', stamp, revision))
