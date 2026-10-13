import tempfile
import unittest
from pathlib import Path
from app.store import Store
from app.worker import Worker
from app.adapters.fake_destination import FakeDestination
from app.approvals import decide

class FoundationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 1000.0
        self.store = Store(self.root / 'state.sqlite', clock=lambda: self.now)
        self.dest = FakeDestination(self.root / 'destination.sqlite')
        self.store.seed()

    def tearDown(self):
        self.dest.close()
        self.store.close()
        self.temp.cleanup()

    def run_worker(self, **kwargs):
        Worker(self.store, self.dest, **kwargs).run()

    def approval(self):
        return self.store.db.execute('SELECT id FROM approvals ORDER BY rowid DESC LIMIT 1').fetchone()[0]

    def approve(self):
        decide(self.store, self.approval(), 'approved')

    def receipt_count(self):
        return self.dest.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
