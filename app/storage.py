from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def protect_directory(directory):
    """Restrict new state files to the running Windows user and SYSTEM."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        import ntsecuritycon
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
        acl = win32security.ACL()
        flags = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        for identity in [sid, system]:
            acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, flags, ntsecuritycon.FILE_ALL_ACCESS, identity)
        win32security.SetNamedSecurityInfo(str(directory), win32security.SE_FILE_OBJECT,
                                          win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
                                          None, None, acl, None)
    else:
        directory.chmod(0o700)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Vault:
    """Atomic Windows user-bound DPAPI storage. No plaintext fallback on Windows."""
    def __init__(self, directory: Path):
        self.directory = directory
        protect_directory(directory)
        self.path = directory / 'secrets.dpapi'
        self.lock = threading.RLock()

    @contextmanager
    def guarded(self):
        # Serialize CLI and server updates as well as threads (DPAPI file replacement alone
        # cannot prevent a lost update when two providers refresh concurrently).
        with self.lock:
            handle = None
            if os.name == 'nt':
                import win32event, win32api
                name = 'Local\\TonyRestaurantVault-' + hashlib.sha256(str(self.path.resolve()).lower().encode()).hexdigest()
                handle = win32event.CreateMutex(None, False, name)
                result = win32event.WaitForSingleObject(handle, 30000)
                if result not in (0, 128):
                    win32api.CloseHandle(handle)
                    raise RuntimeError('Credential vault busy; retry later.')
            try:
                yield
            finally:
                if handle is not None:
                    win32event.ReleaseMutex(handle)
                    win32api.CloseHandle(handle)

    def read(self):
        with self.guarded():
            if not self.path.exists():
                return {}
            if os.name != 'nt':
                raise RuntimeError('Credential vault requires Windows DPAPI.')
            import win32crypt
            raw = win32crypt.CryptUnprotectData(self.path.read_bytes(), None, None, None, 0)[1]
            return json.loads(raw)

    def write(self, value):
        with self.guarded():
            if os.name != 'nt':
                raise RuntimeError('Credential vault requires Windows DPAPI.')
            import win32crypt
            raw = win32crypt.CryptProtectData(json.dumps(value).encode(), 'TonyRestaurantOps', None, None, None, 0)
            temp = self.path.with_suffix('.tmp')
            temp.write_bytes(raw)
            os.replace(temp, self.path)

    def update(self, **values):
        with self.guarded():
            current = self.read()
            current.update(values)
            self.write(current)


class DB:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'operations.sqlite3'
        with self.connect() as c:
            c.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS memory (id INTEGER PRIMARY KEY, text TEXT NOT NULL,
                    kind TEXT NOT NULL, expires TEXT, done INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, channel TEXT NOT NULL,
                    role TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS briefings (id INTEGER PRIMARY KEY, day TEXT NOT NULL,
                    mode TEXT NOT NULL, text TEXT NOT NULL, ai INTEGER NOT NULL, created TEXT NOT NULL,
                    UNIQUE(day, mode));
                CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, mode TEXT NOT NULL,
                    payload TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS webhook_receipts (digest TEXT PRIMARY KEY, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS clover_days (
                    day TEXT PRIMARY KEY, paid_orders INTEGER NOT NULL,
                    gross_line_cents INTEGER NOT NULL, custom_lines INTEGER NOT NULL,
                    refunded_orders INTEGER NOT NULL, imported_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS clover_item_days (
                    day TEXT NOT NULL, item_id TEXT NOT NULL, name TEXT NOT NULL,
                    quantity INTEGER NOT NULL, gross_line_cents INTEGER NOT NULL,
                    PRIMARY KEY(day, item_id));
                CREATE TABLE IF NOT EXISTS clover_periods (
                    start_day TEXT PRIMARY KEY, end_day TEXT NOT NULL,
                    order_count INTEGER NOT NULL, imported_at TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def get(self, key, default=None):
        with self.connect() as c:
            row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def rows(self, sql, args=()):
        with self.connect() as c:
            return [dict(row) for row in c.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.connect() as c:
            return c.execute(sql, args).lastrowid

    def memories(self):
        return self.rows('SELECT * FROM memory WHERE expires IS NULL OR expires>? ORDER BY id DESC', (utcnow(),))

    def message(self, channel, role, text):
        self.execute('INSERT INTO messages(channel,role,text,created) VALUES (?,?,?,?)', (channel, role, text, utcnow()))
        # Keep a bounded local history; never store raw Toast payloads.
        self.execute('DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 2000)')

    def history(self, channel):
        return list(reversed(self.rows('SELECT * FROM messages WHERE channel=? ORDER BY id DESC LIMIT 40', (channel,))))

    def backup(self):
        target = self.path.parent / 'backups'
        target.mkdir(exist_ok=True)
        dest = target / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(2) + '.sqlite3')
        with self.connect() as source, sqlite3.connect(dest) as out:
            source.backup(out)
        for old in sorted(target.glob('*.sqlite3'))[:-14]:
            old.unlink()
        return dest.name
