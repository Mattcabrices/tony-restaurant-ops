import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(text, limit=2000):
    return ' '.join(re.sub(r'<[^>]*>', ' ', str(text)).split())[:limit]


METRICS = {
    'toast.net_sales': 'USD', 'toast.hourly_sales': 'USD', 'toast.evening_sales': 'USD',
    'toast.channel_sales': 'USD', 'toast.orders': 'orders', 'toast.labor_hours': 'hours',
    'toast.labor_cost': 'USD', 'toast.item_portions': 'portions', 'toast.item_pieces': 'pieces',
    'clover.gross_line_sales': 'USD', 'clover.paid_orders': 'orders',
    'clover.item_portions': 'portions', 'weather.temperature_c': 'C',
    'weather.precipitation_mm': 'mm', 'weather.temperature_max_c':'C', 'weather.temperature_min_c':'C',
}


MIGRATIONS = [
    '''CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    CREATE TABLE datasets(id INTEGER PRIMARY KEY, source TEXT NOT NULL, retrieved_at TEXT NOT NULL,
      sha256 TEXT NOT NULL, metadata TEXT NOT NULL, UNIQUE(source,sha256));
    CREATE TABLE observations(id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets(id),
      day TEXT NOT NULL, metric TEXT NOT NULL, dimension TEXT NOT NULL, value REAL NOT NULL,
      available_at TEXT NOT NULL, quality TEXT NOT NULL, UNIQUE(dataset_id,day,metric,dimension));
    CREATE INDEX observation_lookup ON observations(metric,dimension,day,available_at);
    CREATE TABLE journal(id INTEGER PRIMARY KEY, day TEXT NOT NULL, kind TEXT NOT NULL,
      text TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(day,kind,text));
    CREATE TABLE hypotheses(id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
      status TEXT NOT NULL, confidence TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE hypothesis_revisions(id INTEGER PRIMARY KEY, hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),
      status TEXT NOT NULL, confidence TEXT NOT NULL, evidence TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE predictions(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, target_day TEXT NOT NULL,
      metric TEXT NOT NULL, dimension TEXT NOT NULL, horizon_days INTEGER NOT NULL, value REAL NOT NULL,
      low REAL NOT NULL, high REAL NOT NULL, model_version TEXT NOT NULL, evidence TEXT NOT NULL,
      UNIQUE(created_at,target_day,metric,dimension,model_version));
    CREATE TABLE scorecards(id INTEGER PRIMARY KEY, prediction_id INTEGER NOT NULL REFERENCES predictions(id),
      observation_id INTEGER NOT NULL REFERENCES observations(id), actual REAL NOT NULL, error REAL NOT NULL,
      absolute_error REAL NOT NULL, covered INTEGER NOT NULL, scored_at TEXT NOT NULL,
      UNIQUE(prediction_id,observation_id));
    CREATE TABLE experiments(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
      config TEXT NOT NULL, input_sha256 TEXT NOT NULL, result TEXT NOT NULL, model_version TEXT NOT NULL);
    CREATE TABLE calibrations(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, metric TEXT NOT NULL,
      dimension TEXT NOT NULL, model_version TEXT NOT NULL, sample_count INTEGER NOT NULL,
      mae REAL, wape REAL, bias REAL, coverage REAL, experiment_id INTEGER REFERENCES experiments(id));
    CREATE TABLE events(id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_url TEXT NOT NULL,
      day TEXT NOT NULL, label TEXT NOT NULL, category TEXT NOT NULL, retrieved_at TEXT NOT NULL,
      content_sha256 TEXT NOT NULL, trust TEXT NOT NULL DEFAULT 'untrusted source data',
      UNIQUE(source,day,content_sha256,retrieved_at));
    CREATE TABLE runs(id INTEGER PRIMARY KEY, run_key TEXT UNIQUE NOT NULL, started_at TEXT NOT NULL,
      finished_at TEXT, status TEXT NOT NULL, detail TEXT NOT NULL);
    CREATE TABLE connector_state(source TEXT PRIMARY KEY, checked_at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL);''',
    '''CREATE TABLE budgets(day TEXT NOT NULL,kind TEXT NOT NULL,used INTEGER NOT NULL,PRIMARY KEY(day,kind));''',
    '''ALTER TABLE predictions ADD COLUMN run_key TEXT;
    CREATE UNIQUE INDEX prediction_run_series ON predictions(run_key,metric,dimension,model_version);
    ALTER TABLE experiments ADD COLUMN run_key TEXT;
    ALTER TABLE experiments ADD COLUMN series_key TEXT;
    CREATE UNIQUE INDEX experiment_run_series ON experiments(run_key,series_key);''',
    '''CREATE TABLE research_plans(id INTEGER PRIMARY KEY,plan_key TEXT UNIQUE NOT NULL,
      hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id),created_at TEXT NOT NULL,
      template TEXT NOT NULL,metric TEXT NOT NULL,dimension TEXT NOT NULL,status TEXT NOT NULL);
    CREATE TABLE research_plan_runs(id INTEGER PRIMARY KEY,plan_id INTEGER NOT NULL REFERENCES research_plans(id),
      day TEXT NOT NULL,started_at TEXT NOT NULL,status TEXT NOT NULL,input_sha256 TEXT NOT NULL,
      experiment_id INTEGER REFERENCES experiments(id),result TEXT NOT NULL,UNIQUE(plan_id,day));''',
    '''CREATE TABLE feature_vintages(id INTEGER PRIMARY KEY,feature TEXT NOT NULL,day TEXT NOT NULL,
      value REAL NOT NULL,available_at TEXT NOT NULL,source_url TEXT NOT NULL,kind TEXT NOT NULL,
      provenance TEXT NOT NULL,content_sha256 TEXT NOT NULL,UNIQUE(feature,day,available_at,content_sha256));
    CREATE INDEX feature_asof ON feature_vintages(feature,day,available_at);''',
    '''CREATE TABLE item_count_snapshots(id INTEGER PRIMARY KEY,source TEXT NOT NULL,day TEXT NOT NULL,
      category TEXT NOT NULL,mapping_version TEXT NOT NULL,mapping_sha256 TEXT NOT NULL,
      quantity REAL,observed_quantity REAL NOT NULL,by_size TEXT NOT NULL,quality TEXT NOT NULL,
      issues TEXT NOT NULL,content_sha256 TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,
      UNIQUE(source,day,category,mapping_sha256,content_sha256));
    CREATE INDEX item_count_lookup ON item_count_snapshots(source,category,day,last_seen_at);''',
    '''ALTER TABLE item_count_snapshots ADD COLUMN calculation_version TEXT NOT NULL DEFAULT 'whole-pizza-v1';
    ALTER TABLE item_count_snapshots ADD COLUMN reconciliation TEXT NOT NULL DEFAULT '{}';''',

    """CREATE TABLE improvements(id INTEGER PRIMARY KEY,task_key TEXT UNIQUE NOT NULL,
      title TEXT NOT NULL,priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 3),
      status TEXT NOT NULL CHECK(status IN ('proposed','queued','blocked','done','dismissed')),
      source TEXT NOT NULL,kind TEXT NOT NULL,evidence TEXT NOT NULL,acceptance TEXT NOT NULL,
      progress TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE improvement_history(id INTEGER PRIMARY KEY,task_id INTEGER NOT NULL REFERENCES improvements(id),
      actor TEXT NOT NULL,status TEXT NOT NULL,note TEXT NOT NULL,created_at TEXT NOT NULL);""",

    """CREATE TABLE approved_tasks(id TEXT PRIMARY KEY,created_at TEXT NOT NULL,manifest TEXT NOT NULL,
      sha256 TEXT NOT NULL,state TEXT NOT NULL,approved_at TEXT,approval_source TEXT,started_at TEXT,
      finished_at TEXT,heartbeat_at TEXT,attempts INTEGER NOT NULL DEFAULT 0,checkpoint TEXT NOT NULL DEFAULT '{}',
      result TEXT NOT NULL DEFAULT '{}',error TEXT);
    CREATE TABLE approved_task_events(id INTEGER PRIMARY KEY,task_id TEXT NOT NULL REFERENCES approved_tasks(id),
      created_at TEXT NOT NULL,state TEXT NOT NULL,note TEXT NOT NULL);""",
]


class CornerStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'research.sqlite3'
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
            version = connection.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0] if exists else 0
            if version > len(MIGRATIONS):
                raise RuntimeError('Research database is newer than this code; rollback requires matching code.')
            for index in range(version, len(MIGRATIONS)):
                # executescript otherwise commits an already-open transaction.
                stamp = now().replace("'", "''")
                connection.executescript('BEGIN IMMEDIATE;\n' + MIGRATIONS[index] +
                    f"\nINSERT INTO schema_migrations VALUES({index+1},'{stamp}');\nPRAGMA user_version={index+1};\nCOMMIT;")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def rows(self, sql, args=()):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, args)]

    def ingest(self, source, rows, metadata, retrieved_at=None):
        if source not in {'Toast', 'Clover', 'NWS', 'NOAA'}:
            raise ValueError('Unknown numeric research source.')
        retrieved_at = retrieved_at or now()
        timestamp = datetime.fromisoformat(retrieved_at)
        if timestamp.tzinfo is None:
            raise ValueError('Retrieval timestamp requires a timezone.')
        retrieved_at = timestamp.astimezone(timezone.utc).isoformat()
        checked = []
        for row in rows:
            if set(row) - {'day', 'metric', 'dimension', 'value', 'quality'}:
                raise ValueError('Research rows contain unapproved fields.')
            date.fromisoformat(row['day'])
            metric, dimension = row['metric'], row.get('dimension', 'all')
            if metric not in METRICS or not metric.startswith({'Toast': 'toast.', 'Clover': 'clover.', 'NWS': 'weather.', 'NOAA': 'weather.'}[source]):
                raise ValueError('Metric does not match its source.')
            if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,80}', dimension):
                raise ValueError('Only nonpersonal dimension keys are accepted.')
            value = row['value']
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError('Research value must be finite numeric data.')
            quality = row.get('quality', 'complete')
            if quality not in {'complete', 'partial', 'unconfirmed_zero', 'reconstructed'}:
                raise ValueError('Unknown data-quality label.')
            checked.append(dict(day=row['day'], metric=metric, dimension=dimension, value=value, quality=quality))
        payload = json.dumps(checked, sort_keys=True, separators=(',', ':'))
        sha = hashlib.sha256((payload + json.dumps(metadata, sort_keys=True)).encode()).hexdigest()
        with self.connect() as connection:
            connection.execute('INSERT OR IGNORE INTO datasets(source,retrieved_at,sha256,metadata) VALUES(?,?,?,?)',
                               (source, retrieved_at, sha, json.dumps(metadata)))
            dataset = connection.execute('SELECT id FROM datasets WHERE source=? AND sha256=?', (source, sha)).fetchone()[0]
            connection.executemany('INSERT OR IGNORE INTO observations(dataset_id,day,metric,dimension,value,available_at,quality) VALUES(?,?,?,?,?,?,?)',
                [(dataset, r['day'], r['metric'], r['dimension'], r['value'], retrieved_at, r['quality']) for r in checked])
            connection.execute('''INSERT INTO connector_state VALUES(?,?,'numeric snapshot',?) ON CONFLICT(source) DO UPDATE SET
                checked_at=excluded.checked_at,status=excluded.status,detail=excluded.detail WHERE excluded.checked_at>=connector_state.checked_at''',
                (source, retrieved_at, json.dumps({'dataset_id': dataset, 'rows': len(checked), 'sha256': sha})))
        return {'dataset_id': dataset, 'rows': len(checked), 'sha256': sha}

    def series(self, metric, dimension='all', as_of=None):
        return self.rows('''SELECT o.*, d.source,d.retrieved_at,d.sha256 FROM observations o
            JOIN datasets d ON d.id=o.dataset_id WHERE o.metric=? AND o.dimension=? AND o.available_at<=?
            AND o.id=(SELECT MAX(x.id) FROM observations x WHERE x.metric=o.metric AND x.dimension=o.dimension
              AND x.day=o.day AND x.available_at<=?) ORDER BY o.day''', (metric, dimension, as_of or now(), as_of or now()))

    def journal(self, day, kind, text, evidence=()):
        date.fromisoformat(day)
        with self.connect() as connection:
            connection.execute('INSERT OR IGNORE INTO journal(day,kind,text,evidence,created_at) VALUES(?,?,?,?,?)',
                               (day, clean(kind, 40), clean(text), json.dumps(evidence), now()))

    def hypothesis(self, key, title, evidence, note, status='proposed', confidence='exploratory'):
        if status not in {'proposed', 'testing', 'supported', 'unsupported', 'retired'} or confidence not in {'exploratory', 'low', 'medium', 'high'}:
            raise ValueError('Invalid hypothesis state.')
        with self.connect() as connection:
            connection.execute('INSERT OR IGNORE INTO hypotheses(key,title,status,confidence,created_at) VALUES(?,?,?,?,?)',
                               (key, clean(title), status, confidence, now()))
            identifier = connection.execute('SELECT id FROM hypotheses WHERE key=?', (key,)).fetchone()[0]
            connection.execute('UPDATE hypotheses SET status=?,confidence=? WHERE id=?', (status, confidence, identifier))
            connection.execute('INSERT INTO hypothesis_revisions(hypothesis_id,status,confidence,evidence,note,created_at) VALUES(?,?,?,?,?,?)',
                               (identifier, status, confidence, json.dumps(evidence), clean(note), now()))
        return identifier
