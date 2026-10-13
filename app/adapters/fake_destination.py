"""A durable local simulation. No network transport exists."""
import sqlite3
from pathlib import Path
from ..models import digest

class Conflict(RuntimeError):
    pass

class FakeDestination:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS receipts(action_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,receipt_id TEXT NOT NULL UNIQUE)')
        self.db.commit()

    def close(self):
        self.db.close()

    def lookup(self, action_id, payload):
        row = self.db.execute('SELECT * FROM receipts WHERE action_id=?', (action_id,)).fetchone()
        if not row:
            return None
        if row['payload_hash'] != digest(payload):
            raise Conflict('Conflicting simulated receipt; reconcile manually')
        return dict(row)

    def submit(self, action_id, payload):
        existing = self.lookup(action_id, payload)
        if existing:
            return existing
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?)', (action_id, digest(payload), 'simulated:' + action_id))
        return self.lookup(action_id, payload)
