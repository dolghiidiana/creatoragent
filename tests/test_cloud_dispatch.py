import json
from app.cloud_dispatch import CloudDispatch, CHECKS
from app.nvidia_synthetic import manifest, response, validate, context_document
from tests.support import FoundationCase

class DispatchTests(FoundationCase):
    def setup_gate(self, enabled=True):
        gate=CloudDispatch(self.store,enabled=enabled)
        gate.memory.save('lantern',context_document())
        gate.import_history('prior-synthetic-ledger','0.01100','Fictional historical ledger for accounting tests')
        payload=manifest(); batch=gate.propose(payload,self.now+1000)
        gate.review(batch,'approved','test reviewer (simulated consent)')
        gate.attest(batch,{k:'simulated evidence for offline unit test' for k in CHECKS},self.now+100)
        return gate,payload,batch

    def call(self,gate,payload,batch,slot=0, transport=None):
        item=payload['slots'][slot]
        return gate.dispatch(batch,item['id'],item['request'],payload['context_hash'],
            transport or (lambda _:response(item['id'].split('-')[0])),
            lambda r:validate(r,item['id'].split('-')[0]))

    def test_disabled_never_touches_transport(self):
        g,p,b=self.setup_gate(False)
        with self.assertRaises(ValueError): self.call(g,p,b,transport=lambda _:self.fail('transport touched'))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dispatch_requests').fetchone()[0],0)

    def test_atomic_reservation_and_settlement(self):
        g,p,b=self.setup_gate(); identifier=self.call(g,p,b)
        row=self.store.db.execute('SELECT * FROM evaluation_reservations WHERE id=?',(identifier,)).fetchone()
        self.assertEqual(row['state'],'settled'); self.assertEqual(row['actual'],'0.00006')
        with self.assertRaises(Exception): self.call(g,p,b)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dispatch_requests').fetchone()[0],1)

    def test_revocation_expiry_and_context(self):
        g,p,b=self.setup_gate()
        with self.assertRaises(ValueError): g.reserve_intent(b,p['slots'][0]['id'],p['slots'][0]['request'],'changed')
        self.now+=101
        with self.assertRaises(ValueError): self.call(g,p,b)
        g.attest(b,{k:'renewed simulated check' for k in CHECKS},self.now+10)
        g.review(b,'revoked','test reviewer')
        with self.assertRaises(ValueError): self.call(g,p,b)

    def test_payload_mutation_and_repair_denied(self):
        g,p,b=self.setup_gate(); p['slots'][0]['request']['temperature']=1
        with self.assertRaises(ValueError): self.call(g,p,b)
        with self.assertRaises(ValueError): self.call(g,p,b,1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM evaluation_reservations').fetchone()[0],0)

    def test_unknown_response_holds_budget_and_blocks(self):
        g,p,b=self.setup_gate()
        def lost(_): raise OSError('simulated lost response')
        with self.assertRaises(OSError): self.call(g,p,b,transport=lost)
        self.assertEqual(self.store.db.execute('SELECT state FROM evaluation_reservations').fetchone()[0],'uncertain')
        with self.assertRaises(ValueError): self.call(g,p,b,2)

    def test_accounting_or_model_mismatch_stops(self):
        g,p,b=self.setup_gate(); r=response('fact'); r['model']='wrong-model'
        with self.assertRaises(ValueError): self.call(g,p,b,transport=lambda _:r)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dispatch_requests').fetchone()[0],1)

    def test_over_limit_accounting_stops(self):
        g,p,b=self.setup_gate(); r=response('fact')
        r['usage']={'prompt_tokens':1,'completion_tokens':2001,'total_tokens':2002}
        with self.assertRaises(ValueError): self.call(g,p,b,transport=lambda _:r)
        self.assertEqual(self.store.db.execute('SELECT state FROM evaluation_reservations').fetchone()[0],'uncertain')

    def test_content_failure_blocks_continuation(self):
        g,p,b=self.setup_gate(); r=response('fact')
        a=json.loads(r['choices'][0]['message']['content']); a['answer']='thirteen'
        r['choices'][0]['message']['content']=json.dumps(a)
        self.call(g,p,b,transport=lambda _:r)
        with self.assertRaises(ValueError): self.call(g,p,b,2)

    def test_manifest_tamper_denied(self):
        g,p,b=self.setup_gate(); p['credit_cap']='0.05'
        self.store.db.execute('UPDATE dispatch_batches SET manifest=? WHERE id=?',(json.dumps(p),b))
        with self.assertRaises(ValueError): self.call(g,p,b)

    def test_stale_context_cannot_be_supplied_to_bypass_revision(self):
        g,p,b=self.setup_gate(); doc=context_document(); doc['goal']='Changed goal'
        g.memory.save('lantern',doc)
        with self.assertRaises(ValueError): self.call(g,p,b)

    def test_failed_reservation_rolls_back_intent(self):
        g,p,b=self.setup_gate()
        self.store.db.execute("CREATE TRIGGER reject_reservation BEFORE INSERT ON evaluation_reservations BEGIN SELECT RAISE(ABORT,'simulated database failure'); END")
        with self.assertRaises(Exception): self.call(g,p,b)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM dispatch_requests').fetchone()[0],0)

    def test_competing_dispatchers_only_one_intent(self):
        import threading
        from app.store import Store
        g,p,b=self.setup_gate(); outcomes=[]; start=threading.Barrier(2)
        def competitor():
            other=Store(self.store.path,clock=lambda:self.now)
            try:
                engine=CloudDispatch(other,enabled=True); start.wait(timeout=5)
                s=p['slots'][0]
                try:
                    engine.reserve_intent(b,s['id'],s['request'],p['context_hash']); outcomes.append('intent')
                except ValueError: outcomes.append('blocked')
            finally: other.close()
        threads=[threading.Thread(target=competitor) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        self.assertEqual(sorted(outcomes),['blocked','intent'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM evaluation_reservations').fetchone()[0],1)

    def test_budget_reservation_includes_history(self):
        g,p,b=self.setup_gate()
        self.store.db.execute("INSERT INTO evaluation_reservations VALUES('older','0.039',NULL,'settled')")
        self.store.db.execute("UPDATE evaluation_reservations SET actual='0.039' WHERE id='older'")
        with self.assertRaises(ValueError): self.call(g,p,b)

    def test_format_repair_bounded_no_content_repair(self):
        g,p,b=self.setup_gate(); r=response('fact'); r['choices'][0]['message']['content']='not json'
        self.call(g,p,b,transport=lambda _:r)
        self.call(g,p,b,1)
        with self.assertRaises(Exception): self.call(g,p,b,1)

    def test_history_cannot_reset_cap(self):
        g,p,b=self.setup_gate()
        with self.assertRaises(ValueError): g.import_history('prior-synthetic-ledger','0','changed')
        with self.assertRaises(ValueError): g.import_history('additional','0.04','other accepted ledger')
        self.assertEqual(str(g._used()),'0.01100')

    def test_restart_preserves_unresolved_intent(self):
        g,p,b=self.setup_gate(); s=p['slots'][0]
        g.reserve_intent(b,s['id'],s['request'],p['context_hash'])
        from app.store import Store
        other=Store(self.store.path,clock=lambda:self.now)
        try:
            with self.assertRaises(ValueError): self.call(CloudDispatch(other,enabled=True),p,b,2)
        finally: other.close()

    def test_exact_fixture_and_semantic_rejection(self):
        from app.nvidia_synthetic import CASES
        for case in CASES: self.assertEqual(validate(response(case['id']),case['id']),'passed')
        r=response('experience'); a=json.loads(r['choices'][0]['message']['content']); a['answer']='Everyone will feel calmer.'
        r['choices'][0]['message']['content']=json.dumps(a)
        self.assertEqual(validate(r,'experience'),'content_failure')
