import copy
from tests.support import FoundationCase
from app.creator_context import CreatorContext, BOUNDARIES, validate_claims
from app.evaluation_gate import EvaluationGate

class CreatorContextTests(FoundationCase):
    def context(self):
        return dict(goal='Prepare a source-backed creator review', purpose='Protect voice and reduce interruptions',
                    priorities=['evidence before promotion'], identity='Sample Creator',
                    voice=['personal reflection'], source_scope=['synthetic-only'], boundaries=sorted(BOUNDARIES),
                    model_policy={'switch_only_after_comparable_evaluation': True})

    def saved(self):
        memory = CreatorContext(self.store)
        doc = self.context()
        doc['identity'] = self.store.project('lantern')['identity']
        memory.save('lantern', doc)
        return memory, doc

    def test_context_durable_identity_and_revision(self):
        memory, doc = self.saved()
        self.assertEqual(memory.save('lantern', doc), 1)
        wrong = copy.deepcopy(doc); wrong['identity'] = 'Another artist'
        with self.assertRaises(ValueError): memory.save('lantern', wrong)
        from app.store import Store
        reopened = Store(self.store.path)
        self.assertEqual(CreatorContext(reopened).current('lantern')['document']['purpose'], doc['purpose'])
        reopened.close()

    def test_batch_reviews_and_invalidate_on_goal_change(self):
        memory, doc = self.saved()
        first = memory.prepare_review('lantern', 'public_posting', 'simulated://one', {'text':'one'})
        memory.prepare_review('lantern', 'outreach', 'simulated://two', {'text':'two'})
        self.assertEqual(len(memory.checkpoint('lantern')['decisions_needed']), 2)
        doc['goal'] = 'New goal'
        memory.save('lantern', doc)
        with self.assertRaises(ValueError): memory.decide(first, 'approved')
        self.assertFalse(memory.checkpoint('lantern')['external_execution_enabled'])

    def test_claims_quote_refs_and_personal_attribution(self):
        sources = {'s1':dict(text='I walked for thirty minutes.',kind='manuscript',personal_experience=True)}
        claim = dict(label='direct_quote',text='I walked for thirty minutes.',source_refs=['s1'],attribution='author')
        self.assertEqual(validate_claims([claim],sources), [])
        claim['source_refs']=[1]
        self.assertTrue(validate_claims([claim],sources))
        claim['source_refs']=['s1']; claim['attribution']=None
        self.assertTrue(validate_claims([claim],sources))
        claim['attribution']='author'; claim['text']='Walking heals everyone.'
        self.assertTrue(validate_claims([claim],sources))

    def test_budget_persists_and_uncertainty_blocks(self):
        gate=EvaluationGate(self.store, '0.005')
        gate.reserve('r1',4000,4000,'0.06','0.24')
        gate.settle('r1',None)
        with self.assertRaises(ValueError): gate.reserve('r2',4000,4000,'0.06','0.24')
        with self.assertRaises(ValueError): EvaluationGate(self.store,'0.05')

    def test_budget_cap_duplicate_and_token_limit(self):
        gate=EvaluationGate(self.store,'0.0012')
        gate.reserve('r1',4000,4000,'0.06','0.24')
        with self.assertRaises(ValueError): gate.reserve('r2',1,1,'0.06','0.24')
        with self.assertRaises(ValueError): gate.reserve('r1',1,1,'0.06','0.24')
        with self.assertRaises(ValueError): gate.reserve('r3',4001,1,'0.06','0.24')
