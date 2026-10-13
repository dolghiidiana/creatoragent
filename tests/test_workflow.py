import json
from tests.support import FoundationCase
from app import operations
from app.approvals import decide
from app.store import Store

class WorkflowTests(FoundationCase):
    def test_wait_branch_then_automatic_completion(self):
        self.run_worker()
        self.assertEqual(self.store.task('lantern:submit')['state'], 'awaiting_approval')
        self.assertEqual(self.store.task('lantern:checklist')['state'], 'completed')
        self.assertEqual(self.receipt_count(), 0)
        self.approve()
        self.run_worker()
        self.assertTrue(all(r[0] == 'completed' for r in self.store.db.execute('SELECT state FROM tasks')))
        self.assertEqual(self.receipt_count(), 1)
        self.assertTrue(self.store.output('lantern:verify')['verified_receipt'])
        self.assertTrue(self.store.output('lantern:followup')['synthetic'])

    def test_pause_persists_and_resume(self):
        self.store.pause(True)
        self.run_worker()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 0)
        other = Store(self.store.path)
        self.assertEqual(other.project('lantern')['paused'], 1)
        other.close()
        self.store.pause(False)
        self.run_worker()
        self.assertEqual(self.store.task('lantern:submit')['state'], 'awaiting_approval')

    def test_rejection_blocks_successors(self):
        self.run_worker()
        decide(self.store, self.approval(), 'rejected')
        self.run_worker()
        self.assertEqual(self.store.task('lantern:submit')['state'], 'cancelled')
        self.assertEqual(self.store.task('lantern:verify')['state'], 'blocked')
        self.assertEqual(self.store.task('lantern:followup')['state'], 'blocked')
        self.assertIn('Dependency', self.store.task('lantern:verify')['reason'])
        self.assertEqual(self.receipt_count(), 0)

    def test_missing_information_and_resumption(self):
        payload = json.loads(self.store.task('lantern:brief')['payload'])
        payload.pop('title')
        self.store.update_payload('lantern:brief', payload)
        self.run_worker()
        self.assertEqual(self.store.task('lantern:brief')['state'], 'awaiting_information')
        self.assertEqual(self.store.task('lantern:announcement')['state'], 'pending')
        payload['title'] = 'The Lantern Notebook'
        self.store.update_payload('lantern:brief', payload)
        self.run_worker()
        self.assertEqual(self.store.task('lantern:brief')['state'], 'completed')

    def test_retry_due_time_and_exhaustion(self):
        def transient(store, task, dest):
            if task['operation'] == 'brief':
                raise OSError('Synthetic temporary failure')
            return operations.execute(store, task, dest)
        self.run_worker(executor=transient, retry_seconds=2)
        task = self.store.task('lantern:brief')
        self.assertEqual(task['state'], 'scheduled')
        self.assertEqual(task['attempts'], 1)
        self.run_worker(executor=transient)
        self.assertEqual(self.store.task('lantern:brief')['attempts'], 1)
        self.now = 1002
        self.run_worker(executor=transient, retry_seconds=2)
        self.assertEqual(self.store.task('lantern:brief')['attempts'], 2)
        self.now = 1006
        self.run_worker(executor=transient, retry_seconds=2)
        self.assertEqual(self.store.task('lantern:brief')['state'], 'blocked')
        self.assertEqual(self.store.task('lantern:announcement')['state'], 'blocked')

    def test_network_modules_confined_to_disabled_runtime_adapter(self):
        import ast
        from pathlib import Path
        forbidden = {'urllib','http','requests','socket','openai','httpx'}
        root = Path(__file__).parents[1] / 'app'
        for source in root.rglob('*.py'):
            if source.name == 'runtime_nebius.py':
                # The newly approved adapter is separately covered by disabled-network tests.
                # Only urllib is allowed here; general workflow modules remain network-free.
                allowed={'urllib'}
            else:
                allowed=set()
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(n.name.split('.')[0] not in forbidden-allowed for n in node.names))
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn((node.module or '').split('.')[0], forbidden-allowed)
        self.assertEqual(self.store.project('lantern')['identity'], 'Sample Creator')
        self.assertEqual(len(list(root.glob('adapters/*.py'))), 2)

    def test_events_append_only(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute('DELETE FROM events')
