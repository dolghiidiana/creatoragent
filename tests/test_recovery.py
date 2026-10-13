import json
import subprocess
import sys
import time
from pathlib import Path
from tests.support import FoundationCase
from app.store import Store
from app.worker import Worker

class RecoveryTests(FoundationCase):
    def cli(self, command, *extra):
        return subprocess.run([sys.executable, '-m', 'app.cli', '--db', str(self.store.path),
                               '--destination-db', str(self.dest.path), command, *extra],
                              cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=10)

    def test_process_crashes_recover_without_duplicate_effects(self):
        # Separate real child processes for each crash point and separate databases per case.
        for point in ('after_claim','before_dispatch','after_effect'):
            with self.subTest(point=point):
                root = self.root / point
                root.mkdir()
                state = root / 'state.sqlite'
                destination = root / 'destination.sqlite'
                prefix = [sys.executable, '-m', 'app.cli', '--db', str(state), '--destination-db', str(destination)]
                def cmd(*args):
                    return subprocess.run(prefix + list(args), cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=10)
                self.assertEqual(cmd('seed').returncode, 0)
                self.assertEqual(cmd('run').returncode, 0)
                store = Store(state)
                request_id = store.db.execute('SELECT id FROM approvals').fetchone()[0]
                store.close()
                self.assertEqual(cmd('approve', request_id).returncode, 0)
                self.assertEqual(cmd('run','--lease-seconds','.01','--crash',point).returncode, 73)
                time.sleep(.025)
                recovered = cmd('run')
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                store = Store(state)
                from app.adapters.fake_destination import FakeDestination
                dest = FakeDestination(destination)
                try:
                    self.assertTrue(all(r[0] == 'completed' for r in store.db.execute('SELECT state FROM tasks')))
                    self.assertEqual(dest.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 1)
                    self.assertEqual(store.db.execute("SELECT COUNT(*) FROM artifacts WHERE task_id='lantern:followup'").fetchone()[0], 1)
                finally:
                    dest.close()
                    store.close()

    def test_restart_waiting_then_approved(self):
        self.run_worker()
        self.store.close()
        self.store = Store(self.root / 'state.sqlite', clock=lambda:self.now)
        self.run_worker()
        self.assertEqual(self.receipt_count(), 0)
        self.approve()
        self.store.close()
        self.store = Store(self.root / 'state.sqlite', clock=lambda:self.now)
        self.run_worker()
        self.assertEqual(self.store.task('lantern:followup')['state'], 'completed')

    def test_conflicting_receipt_blocks(self):
        self.run_worker()
        self.approve()
        task = self.store.task('lantern:submit')
        self.dest.submit(task['action_id'], {'conflicting':True})
        self.run_worker()
        self.assertEqual(self.store.task('lantern:submit')['state'], 'blocked')
        self.assertEqual(self.store.task('lantern:verify')['state'], 'blocked')
        self.assertEqual(self.receipt_count(), 1)

    def test_inconclusive_ledger_blocks_without_submission(self):
        self.run_worker()
        self.approve()
        def unavailable(*args):
            raise RuntimeError('Simulated ledger unavailable; inconclusive')
        self.dest.lookup = unavailable
        self.run_worker()
        self.assertEqual(self.store.task('lantern:submit')['state'], 'blocked')
        self.assertEqual(self.receipt_count(), 0)

    def test_watch_worker_wakes_after_committed_approval(self):
        self.run_worker()
        # Child uses real time, so make the synthetic-clock approval valid for it.
        self.store.db.execute('UPDATE approvals SET expires=?', (time.time()+1000,))
        prefix = [sys.executable, '-m', 'app.cli', '--db', str(self.store.path), '--destination-db', str(self.dest.path)]
        process = subprocess.Popen(prefix + ['run','--watch'], cwd=Path(__file__).parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.approve()
            deadline = time.monotonic() + 5
            while self.store.task('lantern:followup')['state'] != 'completed' and time.monotonic() < deadline:
                time.sleep(.025)
            self.assertEqual(self.store.task('lantern:followup')['state'], 'completed')
            self.assertEqual(self.receipt_count(), 1)
        finally:
            process.terminate()
            process.communicate(timeout=5)
