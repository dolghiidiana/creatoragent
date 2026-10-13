from concurrent.futures import ThreadPoolExecutor
from tests.support import FoundationCase
from app.store import Store
from app.worker import worker_lock

class DeduplicationTests(FoundationCase):
    def test_competing_claims_one_owner_per_task(self):
        # Isolate one eligible task so two claims cannot legitimately claim different work.
        self.store.db.execute("UPDATE tasks SET state='cancelled' WHERE id!='lantern:brief'")
        def claim(owner):
            store = Store(self.store.path)
            try:
                task = store.claim(owner)
                return task['id'] if task else None
            finally:
                store.close()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(claim, ['one','two']))
        self.assertEqual(results.count('lantern:brief'), 1)
        self.assertEqual(results.count(None), 1)

    def test_repeat_seed_approval_and_completed_replay(self):
        self.store.seed()
        self.run_worker()
        self.approve()
        self.approve()
        self.run_worker()
        before = self.store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
        self.store.seed()
        self.run_worker()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 6)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], before)
        self.assertEqual(self.receipt_count(), 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM artifacts WHERE task_id='lantern:followup'").fetchone()[0], 1)

    def test_os_lock_prevents_second_worker(self):
        with worker_lock(self.store.path):
            with self.assertRaises(RuntimeError):
                self.run_worker()

    def test_stale_generation_cannot_commit(self):
        task = self.store.claim('old')
        self.store.db.execute('UPDATE tasks SET generation=generation+1 WHERE id=?', (task['id'],))
        with self.assertRaises(RuntimeError):
            self.store.finish(task, {'stale':True})

    def test_receipt_ledger_is_idempotent(self):
        payload = {'title':'synthetic'}
        first = self.dest.submit('fixed', payload)
        self.assertEqual(self.dest.submit('fixed', payload), first)
        self.assertEqual(self.receipt_count(), 1)
