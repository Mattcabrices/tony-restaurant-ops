"""Small SQLite store for synthetic demonstration records only."""
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
import sqlite3

def utcnow():return datetime.now(timezone.utc).isoformat()

class DB:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def rows(self,query,args=()):
        with self.connect() as c:return [dict(r) for r in c.execute(query,args)]
