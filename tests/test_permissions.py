import json
from tests.support import FoundationCase
from app.approvals import decide
from app.policy import authorize
from app.models import canonical

class PermissionTests(FoundationCase):
    def test_binding_mutations_denied(self):
        self.run_worker()
        self.approve()
        for field, value in [('payload', canonical({'title':'Changed'})),
                             ('destination','simulated://other'), ('operation','unknown'),
                             ('action_id','different-action'), ('artifact_version',99)]:
            with self.subTest(field=field):
                self.store.db.execute('BEGIN IMMEDIATE')
                original = self.store.task('lantern:submit')[field]
                self.store.db.execute('UPDATE tasks SET ' + field + '=? WHERE id=?', (value, 'lantern:submit'))
                disposition, _ = authorize(self.store, self.store.task('lantern:submit'))
                self.assertNotEqual(disposition, 'allowed')
                self.store.db.execute('ROLLBACK')
        self.store.db.execute("UPDATE projects SET identity='Other Creator'")
        self.run_worker()
        self.assertEqual(self.receipt_count(), 0)

    def test_allowed_operation_change_cannot_bypass_submission_approval(self):
        self.run_worker()
        self.approve()
        # Replacing a submit operation with an allowed local operation must not execute it.
        self.store.db.execute("UPDATE tasks SET operation='brief' WHERE id='lantern:submit'")
        self.run_worker()
        self.assertEqual(self.store.task('lantern:submit')['state'], 'blocked')
        self.assertEqual(self.receipt_count(), 0)

    def test_payload_edit_invalidates_approval(self):
        self.run_worker()
        self.approve()
        payload = json.loads(self.store.task('lantern:submit')['payload'])
        payload['title'] = 'Revised synthetic title'
        old = self.approval()
        self.store.update_payload('lantern:submit', payload)
        self.run_worker()
        self.assertEqual(self.store.db.execute('SELECT state FROM approvals WHERE id=?', (old,)).fetchone()[0], 'invalidated')
        self.assertNotEqual(self.approval(), old)
        self.assertEqual(self.receipt_count(), 0)

    def test_expiry_denies_execution(self):
        self.run_worker()
        self.approve()
        old = self.approval()
        self.now += 86401
        self.run_worker()
        self.assertEqual(self.receipt_count(), 0)
        self.assertEqual(self.store.db.execute('SELECT state FROM approvals WHERE id=?', (old,)).fetchone()[0], 'expired')

    def test_revocation_denies_execution(self):
        self.run_worker()
        self.approve()
        decide(self.store, self.approval(), 'revoked')
        self.run_worker()
        self.assertEqual(self.receipt_count(), 0)
        self.assertEqual(self.store.task('lantern:submit')['state'], 'awaiting_approval')

    def test_expired_request_cannot_be_approved(self):
        self.run_worker()
        self.now += 86401
        with self.assertRaises(ValueError):
            self.approve()

    def test_expired_pending_review_is_renewed(self):
        self.run_worker()
        old = self.approval()
        self.now += 86401
        self.run_worker()
        self.assertNotEqual(self.approval(), old)
        self.approve()
        self.run_worker()
        self.assertEqual(self.receipt_count(), 1)

    def test_revoked_review_can_be_replaced_but_not_reused(self):
        self.run_worker()
        old = self.approval()
        self.approve()
        decide(self.store, old, 'revoked')
        self.run_worker()
        self.assertNotEqual(self.approval(), old)
        with self.assertRaises(ValueError):
            decide(self.store, old, 'approved')
        self.approve()
        self.run_worker()
        self.assertEqual(self.receipt_count(), 1)

    def test_historical_review_preserves_exact_content(self):
        from app.approvals import packages
        self.run_worker()
        old_content = packages(self.store)[0]['exact_content']
        self.store.update_payload('lantern:submit', {'title':'Changed synthetic title'})
        self.run_worker()
        reviews = packages(self.store)
        self.assertEqual(reviews[0]['exact_content'], old_content)
        self.assertNotEqual(reviews[-1]['exact_content'], old_content)
