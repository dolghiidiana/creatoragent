import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.store import Store
from app.models import digest
from app.cloud_dispatch import CHECKS
from app.competition_runtime import Runtime,TASK,REVIEW,PAYLOAD_HASH,PROJECT,payload
from app.runtime_nebius import MockTransport,RuntimeNebiusAdapter
from app.nvidia_synthetic import response

class SimulatedCrash(BaseException): pass

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.now=1000.;self.store=Store(self.folder/'state.sqlite',clock=lambda:self.now)
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def runtime(self, raw=None, **kwargs):
        self.transport=MockTransport(raw if raw is not None else response('fact'))
        return Runtime(self.store,transport=self.transport,enabled=True,**kwargs)
    def permit(self,runtime):
        runtime.dispatch.review(runtime.setup['batch'],'approved','offline acceptance test')
        runtime.dispatch.attest(runtime.setup['batch'],{k:'MOCK acceptance evidence' for k in CHECKS},self.now+300)
    def test_payload_is_exact(self):
        self.assertEqual(digest(payload()),PAYLOAD_HASH)
    def test_default_disabled_never_reads_secret(self):
        runtime=Runtime(self.store)
        with patch('pathlib.Path.read_text',side_effect=AssertionError('No file read after setup')):
            runtime.worker().run()
        self.assertEqual(self.store.task(TASK)['state'],'awaiting_approval')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dispatch_requests').fetchone()[0],0)
    def test_no_consent_no_request(self):
        runtime=self.runtime();runtime.worker().run()
        self.assertEqual(self.transport.calls,0)
    def test_no_preflight_no_request(self):
        runtime=self.runtime();runtime.dispatch.review(runtime.setup['batch'],'approved','test')
        runtime.worker().run();self.assertEqual(self.transport.calls,0)
    def test_success_worker_review_restart_dedup(self):
        runtime=self.runtime();self.permit(runtime);runtime.worker().run()
        self.assertEqual(self.store.task(TASK)['state'],'completed')
        self.assertEqual(self.store.task(REVIEW)['state'],'completed')
        first=runtime.receipt();self.assertEqual(first['mode'],'MOCK')
        runtime.worker().run();self.assertEqual(self.transport.calls,1)
        self.store.close();self.store=Store(self.folder/'state.sqlite',clock=lambda:self.now)
        resumed=Runtime(self.store,transport=self.transport,enabled=True);resumed.worker().run()
        self.assertEqual(resumed.receipt(),first);self.assertEqual(self.transport.calls,1)
    def test_crash_after_commit_reuses_without_delivery(self):
        def crash(point):
            if point=='after_receipt_commit': raise SimulatedCrash()
        runtime=self.runtime(crash=crash);self.permit(runtime)
        with self.assertRaises(SimulatedCrash): runtime.worker().run()
        self.now+=31;runtime.crash=None;runtime.worker().run()
        self.assertEqual(self.transport.calls,1);self.assertEqual(self.store.task(REVIEW)['state'],'completed')
    def test_crash_unknown_never_redelivers(self):
        for point in ('after_intent','after_response','before_receipt_commit'):
            with self.subTest(point=point):
                folder=self.folder/point;store=Store(folder/'state.sqlite',clock=lambda:self.now)
                transport=MockTransport(response('fact'))
                def crash(p):
                    if p==point: raise SimulatedCrash()
                try:
                    runtime=Runtime(store,transport=transport,enabled=True,crash=crash);self.permit(runtime)
                    with self.assertRaises(SimulatedCrash): runtime.worker().run()
                    before=transport.calls;self.now+=31;runtime.crash=None;runtime.worker().run()
                    self.assertEqual(transport.calls,before);self.assertIsNone(runtime.receipt())
                    self.assertEqual(store.task(TASK)['state'],'blocked')
                    self.assertEqual(store.db.execute('SELECT state FROM evaluation_reservations').fetchone()[0],'uncertain')
                finally: store.close()
    def test_protected_fields_fail_raw_preserved(self):
        for field,value in [('label','interpretation'),('source_refs',['s2']),('attribution','fictional narrator'),('action','await_approval'),('answer','thirteen')]:
            with self.subTest(field=field):
                store=Store(self.folder/field/'state.sqlite',clock=lambda:self.now)
                raw=response('fact');answer=json.loads(raw['choices'][0]['message']['content']);answer[field]=value
                raw['choices'][0]['message']['content']=json.dumps(answer)
                try:
                    runtime=Runtime(store,transport=MockTransport(raw),enabled=True);self.permit(runtime);runtime.worker().run()
                    self.assertEqual(store.task(TASK)['state'],'blocked')
                    self.assertEqual(runtime.receipt()['raw_response'],raw)
                    self.assertEqual(runtime.receipt()['outcome'],'content_failure')
                finally: store.close()
    def test_surface_comparison_does_not_rewrite_raw(self):
        raw=response('fact');answer=json.loads(raw['choices'][0]['message']['content']);answer['answer']=' twelve. '
        raw['choices'][0]['message']['content']=json.dumps(answer)
        runtime=self.runtime(raw);self.permit(runtime);runtime.worker().run()
        self.assertEqual(runtime.receipt()['raw_response'],raw)
        self.assertEqual(runtime.receipt()['outcome'],'passed')
    def test_accounting_and_identity_uncertainty_hold_budget(self):
        raw=response('fact');raw['model']='wrong'
        runtime=self.runtime(raw);self.permit(runtime);runtime.worker().run()
        self.assertIsNone(runtime.receipt());self.assertEqual(self.store.task(TASK)['state'],'blocked')
        self.assertEqual(self.store.db.execute('SELECT state FROM evaluation_reservations').fetchone()[0],'uncertain')
    def test_duplicate_protected_json_rejected(self):
        raw=response('fact');raw['choices'][0]['message']['content']='{"label":"source_fact","label":"source_fact","answer":"twelve","source_refs":["s1"],"attribution":null,"action":"local_only"}'
        runtime=self.runtime(raw);self.permit(runtime);runtime.worker().run()
        self.assertEqual(runtime.receipt()['outcome'],'content_failure')
    def test_expired_and_revoked_consent(self):
        runtime=self.runtime();self.permit(runtime);runtime.dispatch.review(runtime.setup['batch'],'revoked','test')
        runtime.worker().run();self.assertEqual(self.transport.calls,0)
    def test_expired_preflight(self):
        runtime=self.runtime();self.permit(runtime);self.now+=301;runtime.worker().run()
        self.assertEqual(self.transport.calls,0)
    def test_context_change_blocks(self):
        runtime=self.runtime();self.permit(runtime)
        doc=copy.deepcopy(runtime.dispatch.memory.current(PROJECT)['document']);doc['goal']='changed'
        runtime.dispatch.memory.save(PROJECT,doc);runtime.worker().run();self.assertEqual(self.transport.calls,0)
    def test_task_binding_change_blocks(self):
        runtime=self.runtime();self.permit(runtime)
        self.store.db.execute('UPDATE tasks SET payload=? WHERE id=?',('{}',TASK))
        runtime.worker().run();self.assertEqual(self.transport.calls,0)
    def test_mock_cannot_be_relabelled_live(self):
        self.runtime()
        with self.assertRaises(ValueError): Runtime(self.store,transport=RuntimeNebiusAdapter(),enabled=False)
    def test_budget_and_request_bound(self):
        runtime=self.runtime();self.permit(runtime);runtime.worker().run()
        used,count=runtime.dispatch.group_usage(PROJECT)
        self.assertLessEqual(used,__import__('decimal').Decimal('0.001'));self.assertEqual(count,1)
        with self.assertRaises(ValueError): runtime.dispatch.reserve_intent(runtime.setup['batch'],'fact-1',payload(),runtime.setup['manifest']['context_hash'])
        self.assertEqual(self.transport.calls,1)
    def test_receipt_immutable_and_panel_excludes_reasoning(self):
        raw=response('fact');raw['choices'][0]['message']['reasoning_content']='PRIVATE_PROVIDER_REASONING'
        runtime=self.runtime(raw);self.permit(runtime);runtime.worker().run()
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError): self.store.db.execute('UPDATE runtime_receipts SET body=?',('{}',))
        panel=runtime.panel(self.folder/'panel')
        self.assertNotIn('PRIVATE_PROVIDER_REASONING',json.dumps(panel))
        self.assertIn('LIVE_NEBIUS',panel['evidence_legend']);self.assertEqual(panel['runtime']['mode'],'MOCK')
    def test_disabled_adapter_cannot_open_network(self):
        adapter=RuntimeNebiusAdapter()
        with patch('urllib.request.build_opener',side_effect=AssertionError('Network forbidden')):
            with self.assertRaises(ValueError): adapter(payload())
    def test_live_mock_attestation_cannot_pass_billing_gate(self):
        runtime=Runtime(self.store,transport=RuntimeNebiusAdapter(enabled=True),enabled=True)
        self.permit(runtime)
        with patch('urllib.request.build_opener',side_effect=AssertionError('Network forbidden')):
            runtime.worker().run()
        self.assertEqual(self.store.task(TASK)['state'],'awaiting_approval')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dispatch_requests').fetchone()[0],0)
    def test_pending_billing_attestation_rejected(self):
        runtime=Runtime(self.store,transport=RuntimeNebiusAdapter(),enabled=False)
        evidence=dict(model=runtime.setup['manifest']['model'],endpoint=runtime.setup['manifest']['endpoint'],
            authenticated_model_available=True,zdr_enabled=True,stop_after_trial=True,
            post_v8_billing_reconciled=False,credit_balance='25',trial_balance='0.98',
            input_rate='0.06',output_rate='0.24',input_count=632,completion_includes_reasoning=True,
            payload_hash=PAYLOAD_HASH,observed_at=self.now,expires=self.now+300,references=['offline test'])
        with self.assertRaises(ValueError): runtime.attest_live_preflight(evidence)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM runtime_preflight').fetchone()[0],0)
    def test_atomic_task_binding_rechecked_at_intent(self):
        runtime=self.runtime();self.permit(runtime)
        task=self.store.claim('test',30)
        self.store.db.execute('UPDATE tasks SET destination=? WHERE id=?',('elsewhere',TASK))
        with self.assertRaises(ValueError): runtime.execute(self.store,task,None)
        self.assertEqual(self.transport.calls,0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dispatch_requests').fetchone()[0],0)
    def test_failed_response_observed_but_not_accepted(self):
        raw=response('fact');raw['usage']['completion_tokens']=2001;raw['usage']['total_tokens']=2601
        runtime=self.runtime(raw);self.permit(runtime);runtime.worker().run()
        observed=self.store.db.execute('SELECT body FROM runtime_observations').fetchone()
        self.assertEqual(json.loads(observed[0])['raw_response'],raw);self.assertIsNone(runtime.receipt())
    def test_runtime_worker_does_not_claim_other_project_tasks(self):
        self.store.seed();runtime=self.runtime();self.permit(runtime);runtime.worker().run()
        self.assertEqual(self.store.task(TASK)['state'],'completed')
        self.assertEqual(self.store.task('lantern:brief')['attempts'],0)
        self.assertNotEqual(self.store.task('lantern:brief')['state'],'blocked')
    def test_budget_failure_rolls_back_bound_intent(self):
        runtime=self.runtime();self.permit(runtime)
        self.store.db.execute('INSERT INTO evaluation_reservations VALUES(?,?,?,?)',('earlier','0.04995','0.04995','settled'))
        runtime.worker().run()
        self.assertEqual(self.transport.calls,0);self.assertEqual(self.store.task(TASK)['state'],'blocked')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM runtime_intent_bindings').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dispatch_requests').fetchone()[0],0)
    def test_expired_consent_never_delivers(self):
        runtime=self.runtime();self.permit(runtime);self.now+=86401;runtime.worker().run()
        self.assertEqual(self.transport.calls,0)
    def test_worker_lock_prevents_concurrent_dispatch(self):
        from app.worker import worker_lock
        runtime=self.runtime();self.permit(runtime)
        with worker_lock(self.store.path):
            with self.assertRaises(RuntimeError):runtime.worker().run()
        self.assertEqual(self.transport.calls,0)
        runtime.worker().run();self.assertEqual(self.transport.calls,1)
    def test_response_cannot_promote_mock_to_live(self):
        raw=response('fact');raw['mode']='LIVE_NEBIUS'
        runtime=self.runtime(raw);self.permit(runtime);runtime.worker().run()
        self.assertEqual(runtime.receipt()['mode'],'MOCK')
