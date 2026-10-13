"""Actual worker integration; independent one-call stage, transport OFF by default.

Intent is at-most-once, not exactly-once HTTP delivery. Unknown delivery blocks.
Validated raw envelope + diagnostics + usage settle in one SQLite transaction.
"""
import argparse
import json
from decimal import Decimal
from html import escape
from pathlib import Path
from .models import canonical, digest
from .store import Store
from .worker import Worker
from .cloud_dispatch import CloudDispatch
from .creator_context import CreatorContext
from .nvidia_synthetic import context_document, response as synthetic_response
from .nebius_contract import ENDPOINT, MODEL
from .runtime_contract import assess_local_response, compile_metadata
from .runtime_nebius import DisabledTransport, MockTransport, RuntimeNebiusAdapter

VERSION='competition-runtime-worker-1'
PAYLOAD_HASH='dea1c1da6a992a4dcd318c2af3bde29fc540681393b1d8fea05dba3897d8b889'
PROJECT='competition-runtime-validation-1'
TASK=PROJECT+':synthetic_evidence_audit'
REVIEW=PROJECT+':local_review'

def payload():
    request=json.loads((Path(__file__).parents[1]/'tests/fixtures/competition-runtime-request.json').read_text(encoding='utf-8-sig'))
    if digest(request)!=PAYLOAD_HASH: raise ValueError('Exact saved payload differs')
    return request

class Runtime:
    def __init__(self, store, *, transport=None, enabled=False, crash=None):
        self.store, self.transport, self.crash = store, transport or DisabledTransport(), crash
        # Only the dedicated adapter or explicit MOCK is executable. Response fields cannot set mode.
        if type(self.transport) not in (DisabledTransport,MockTransport,RuntimeNebiusAdapter):
            raise ValueError('Unrecognized executor; no live provenance claim')
        # Planned transport identity; a panel shows LIVE only after an actual persisted adapter result.
        self.mode='MOCK' if type(self.transport) is MockTransport else 'LIVE_NEBIUS'
        self.dispatch=CloudDispatch(store,enabled=enabled)
        store.db.executescript('''
          CREATE TABLE IF NOT EXISTS runtime_setup(id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS runtime_receipts(id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS runtime_observations(id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS runtime_preflight(id TEXT PRIMARY KEY, batch TEXT NOT NULL, body TEXT NOT NULL, expires REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS runtime_intent_bindings(id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL);
          CREATE TRIGGER IF NOT EXISTS runtime_intent_no_update BEFORE UPDATE ON runtime_intent_bindings
            BEGIN SELECT RAISE(ABORT,'runtime intent binding immutable'); END;
          CREATE TRIGGER IF NOT EXISTS runtime_intent_no_delete BEFORE DELETE ON runtime_intent_bindings
            BEGIN SELECT RAISE(ABORT,'runtime intent binding immutable'); END;
          CREATE TRIGGER IF NOT EXISTS runtime_preflight_no_update BEFORE UPDATE ON runtime_preflight
            BEGIN SELECT RAISE(ABORT,'preflight evidence immutable'); END;
          CREATE TRIGGER IF NOT EXISTS runtime_preflight_no_delete BEFORE DELETE ON runtime_preflight
            BEGIN SELECT RAISE(ABORT,'preflight evidence immutable'); END;
          CREATE TRIGGER IF NOT EXISTS runtime_observation_no_update BEFORE UPDATE ON runtime_observations
            BEGIN SELECT RAISE(ABORT,'runtime observation immutable'); END;
          CREATE TRIGGER IF NOT EXISTS runtime_observation_no_delete BEFORE DELETE ON runtime_observations
            BEGIN SELECT RAISE(ABORT,'runtime observation immutable'); END;
          CREATE TRIGGER IF NOT EXISTS runtime_setup_no_update BEFORE UPDATE ON runtime_setup
            BEGIN SELECT RAISE(ABORT,'runtime setup immutable'); END;
          CREATE TRIGGER IF NOT EXISTS runtime_setup_no_delete BEFORE DELETE ON runtime_setup
            BEGIN SELECT RAISE(ABORT,'runtime setup immutable'); END;
          CREATE TRIGGER IF NOT EXISTS runtime_receipt_no_update BEFORE UPDATE ON runtime_receipts
            BEGIN SELECT RAISE(ABORT,'runtime receipt immutable'); END;
          CREATE TRIGGER IF NOT EXISTS runtime_receipt_no_delete BEFORE DELETE ON runtime_receipts
            BEGIN SELECT RAISE(ABORT,'runtime receipt immutable'); END;
        ''')
        self.prepare()

    def prepare(self):
        request=payload(); metadata=compile_metadata(request)
        with self.store.tx():
            self.store.db.execute('INSERT OR IGNORE INTO projects(id,identity,workflow_version,fixture_hash,allowed_operations,allowed_destination) VALUES(?,?,?,?,?,?)',
                (PROJECT,'Sample Creator',1,PAYLOAD_HASH,canonical(['synthetic_evidence_audit','local_review']),ENDPOINT))
        memory=CreatorContext(self.store)
        if not self.store.db.execute('SELECT 1 FROM creator_context WHERE project_id=?',(PROJECT,)).fetchone():
            memory.save(PROJECT,context_document())
        previous=self.store.db.execute('SELECT * FROM runtime_setup WHERE id=?',(PROJECT,)).fetchone()
        if previous:
            if digest(json.loads(previous['body']))!=previous['hash']: raise ValueError('Runtime setup integrity failed')
            self.setup=json.loads(previous['body'])
            if self.setup['mode']!=self.mode: raise ValueError('Cannot relabel MOCK state as live (or vice versa)')
            return
        context_hash=memory.current(PROJECT)['hash']
        manifest=dict(project_id=PROJECT,endpoint=ENDPOINT,model=MODEL,context_hash=context_hash,
            data_scope='original-public-synthetic-v1',credit_cap='0.001',input_rate='0.06',output_rate='0.24',
            slots=[dict(id='fact-1',parent=None,input_limit=4000,request=request)])
        batch=self.dispatch.propose(manifest,self.store.clock()+86400)
        self.dispatch.bind_group(PROJECT,[batch],cap='0.001',maximum_requests=1)
        self.setup=dict(version=VERSION,batch=batch,manifest=manifest,metadata=metadata,mode=self.mode)
        task_payload=dict(runtime_setup_hash=digest(self.setup),batch=batch,payload_hash=PAYLOAD_HASH)
        with self.store.tx():
            self.store.db.execute('INSERT INTO runtime_setup VALUES(?,?,?)',(PROJECT,canonical(self.setup),digest(self.setup)))
            for identifier,operation,destination in [(TASK,'synthetic_evidence_audit',ENDPOINT),(REVIEW,'local_review','local')]:
                self.store.db.execute('INSERT INTO tasks(id,project_id,operation,destination,payload,action_id,state,max_attempts) VALUES(?,?,?,?,?,?,?,?)',
                    (identifier,PROJECT,operation,destination,canonical(task_payload),identifier+':v1','pending',1))
            self.store.db.execute('INSERT INTO dependencies VALUES(?,?)',(REVIEW,TASK))

    def receipt(self):
        row=self.store.db.execute('SELECT * FROM runtime_receipts WHERE id=?',(self.request_id,)).fetchone()
        if not row: return None
        body=json.loads(row['body'])
        if digest(body)!=row['hash'] or body['payload_hash']!=PAYLOAD_HASH or body['mode']!=self.mode:
            raise ValueError('Receipt provenance integrity failed')
        return body

    @property
    def request_id(self): return digest(dict(batch=self.setup['batch'],slot='fact-1'))

    def attest_live_preflight(self,evidence):
        """Trusted operator evidence, not a provider-state discovery or self-attestation."""
        required={'model','endpoint','authenticated_model_available','zdr_enabled','stop_after_trial',
            'post_v8_billing_reconciled','credit_balance','trial_balance','input_rate','output_rate',
            'input_count','completion_includes_reasoning','payload_hash','observed_at','expires','references'}
        if self.mode!='LIVE_NEBIUS' or set(evidence)!=required: raise ValueError('Live preflight scope invalid')
        if (evidence['model']!=MODEL or evidence['endpoint']!=ENDPOINT or evidence['payload_hash']!=PAYLOAD_HASH
            or any(evidence[k] is not True for k in ('authenticated_model_available','zdr_enabled','stop_after_trial','post_v8_billing_reconciled','completion_includes_reasoning'))
            or evidence['input_rate']!='0.06' or evidence['output_rate']!='0.24'
            or type(evidence['input_count']) is not int or not 0<evidence['input_count']<=4000
            or not isinstance(evidence['references'],list) or not evidence['references']
            or any(not isinstance(ref,str) or not ref.strip() for ref in evidence['references'])
            or not self.store.clock()-3600<=evidence['observed_at']<=self.store.clock()
            or not self.store.clock()<evidence['expires']<=evidence['observed_at']+3600):
            raise ValueError('Fresh model/privacy/billing/token evidence required')
        balances=[Decimal(str(evidence[k])) for k in ('credit_balance','trial_balance')]
        if any(not b.is_finite() for b in balances) or min(balances)<Decimal('0.001'):
            raise ValueError('Positive credit AND active trial headroom required; no card spending')
        identifier=digest(evidence)
        with self.store.tx():
            self.store.db.execute('INSERT OR IGNORE INTO runtime_preflight VALUES(?,?,?,?)',
                (identifier,self.setup['batch'],canonical(evidence),evidence['expires']))
        from .cloud_dispatch import CHECKS
        self.dispatch.attest(self.setup['batch'],{k:'Reviewed live evidence '+identifier for k in CHECKS},evidence['expires'])

    def authorize(self,store,task):
        if task['project_id']!=PROJECT: return 'blocked','Runtime worker is scoped to its own project'
        project=store.project(PROJECT)
        if (project['identity']!='Sample Creator' or project['fixture_hash']!=PAYLOAD_HASH
            or project['allowed_destination']!=ENDPOINT): return 'blocked','Project provenance changed'
        if project['paused']: return 'paused','Project paused'
        expected=dict(runtime_setup_hash=digest(self.setup),batch=self.setup['batch'],payload_hash=PAYLOAD_HASH)
        destination=ENDPOINT if task['id']==TASK else 'local'
        if (task['id'] not in (TASK,REVIEW) or task['destination']!=destination
            or json.loads(task['payload'])!=expected or task['artifact_version']!=1
            or task['operation']!=('synthetic_evidence_audit' if task['id']==TASK else 'local_review')):
            return 'blocked','Worker binding changed'
        if self.receipt(): return 'allowed',None
        intent=store.db.execute('SELECT 1 FROM dispatch_requests WHERE id=?',(self.request_id,)).fetchone()
        if intent: return 'blocked','Delivery unresolved or rejected; never redeliver'
        if self.dispatch.memory.current(PROJECT)['hash']!=self.setup['manifest']['context_hash']:
            return 'blocked','Context changed after proposal'
        batch=store.db.execute('SELECT * FROM dispatch_batches WHERE id=?',(self.setup['batch'],)).fetchone()
        checks=store.db.execute('SELECT * FROM dispatch_checks WHERE batch=?',(self.setup['batch'],)).fetchone()
        if batch['state']!='approved' or batch['expires']<=store.clock():
            return 'awaiting_approval','Exact runtime consent pending, expired or revoked'
        if not checks or checks['expires']<=store.clock(): return 'awaiting_approval','Fresh preflight required'
        if not self.dispatch.enabled or type(self.transport) is DisabledTransport:
            return 'awaiting_approval','Transport disabled; no inference authorized by implementation'
        if type(self.transport) is RuntimeNebiusAdapter and not self.transport.enabled:
            return 'awaiting_approval','Dedicated adapter remains disabled'
        if self.mode=='LIVE_NEBIUS':
            proof=store.db.execute('SELECT id FROM runtime_preflight WHERE batch=? AND expires>? ORDER BY expires DESC LIMIT 1',
                                  (self.setup['batch'],store.clock())).fetchone()
            if not proof or any(v!='Reviewed live evidence '+proof[0] for v in json.loads(checks['evidence']).values()):
                return 'awaiting_approval','Reconciled live billing/privacy/model preflight required'
        return 'allowed',None

    def request_review(self,store,task):
        store.event(task['id'],'runtime_review_needed',{'batch':self.setup['batch'],'payload_hash':PAYLOAD_HASH})

    def execute(self,store,task,destination):
        cached=self.receipt()
        if cached:
            if cached['outcome']!='passed': raise ValueError('Persisted failed result; stop')
            return self.public_receipt(cached),self.request_id
        if task['id']!=TASK: raise ValueError('Review requires persisted validated receipt')
        def guard():
            if not store.owns(task) or self.authorize(store,store.task(task['id']))[0]!='allowed':
                raise ValueError('Atomic worker consent/context binding failed')
            consent=dict(store.db.execute('SELECT * FROM dispatch_batches WHERE id=?',(self.setup['batch'],)).fetchone())
            checks=dict(store.db.execute('SELECT * FROM dispatch_checks WHERE batch=?',(self.setup['batch'],)).fetchone())
            binding=dict(task_id=task['id'],action_id=task['action_id'],generation=task['generation'],
                mode=self.mode,payload_hash=PAYLOAD_HASH,metadata_hash=digest(self.setup['metadata']),
                context_hash=self.setup['manifest']['context_hash'],consent=consent,preflight=checks,
                budget_cap='0.001',maximum_requests=1,committed_at=store.clock())
            store.db.execute('INSERT INTO runtime_intent_bindings VALUES(?,?,?)',
                             (self.request_id,canonical(binding),digest(binding)))
        self.dispatch.intent_guard=guard
        identifier=self.dispatch.reserve_intent(self.setup['batch'],'fact-1',payload(),self.setup['manifest']['context_hash'])
        try:
            if self.crash: self.crash('after_intent')
            response=self.transport(payload())
            if self.crash: self.crash('after_response')
            # Preserve even a response with uncertain accounting. It cannot authorize a result.
            observation=dict(raw_response=response,raw_response_hash=digest(response),mode=self.mode,
                             payload_hash=PAYLOAD_HASH,observed_at=store.clock(),
                             provider_wire_json=self.transport.wire_response if type(self.transport) is RuntimeNebiusAdapter else None)
            with store.tx():
                store.db.execute('INSERT INTO runtime_observations VALUES(?,?,?)',
                                 (identifier,canonical(observation),digest(observation)))
            diagnostic={}
            def validate(response):
                try:
                    diagnostic.update(assess_local_response(response,payload()))
                    return 'passed'
                except (ValueError,KeyError,TypeError):
                    diagnostic.update(outcome='rejected',reason='Strict runtime contract rejected; raw preserved')
                    return 'content_failure'
            def persist(identifier,response,outcome,cost):
                binding=store.db.execute('SELECT body,hash FROM runtime_intent_bindings WHERE id=?',(identifier,)).fetchone()
                if not binding or digest(json.loads(binding['body']))!=binding['hash']:
                    raise ValueError('Intent provenance invalid')
                receipt=dict(version=VERSION,request_id=identifier,mode=self.mode,endpoint=ENDPOINT,
                    model=MODEL,batch=self.setup['batch'],payload_hash=PAYLOAD_HASH,
                    context_hash=self.setup['manifest']['context_hash'],metadata=self.setup['metadata'],
                    raw_response=response,raw_response_hash=digest(response),diagnostic=diagnostic,
                    provider_wire_json=observation['provider_wire_json'],
                    intent_binding=json.loads(binding['body']),intent_binding_hash=binding['hash'],
                    outcome=outcome,usage=response['usage'],credit_usd=cost,observed_at=store.clock(),
                    proof='client-observed runtime transport; not provider attestation' if self.mode=='LIVE_NEBIUS' else 'local mock, no provider call')
                store.db.execute('INSERT INTO runtime_receipts VALUES(?,?,?)',(identifier,canonical(receipt),digest(receipt)))
                if self.crash: self.crash('before_receipt_commit')
            self.dispatch.settle(identifier,response,validate,persist_receipt=persist)
            if self.crash: self.crash('after_receipt_commit')
        except BaseException as error:
            with store.tx():
                store.db.execute("UPDATE evaluation_reservations SET state='uncertain' WHERE id=? AND state='reserved'",(identifier,))
                store.db.execute("UPDATE dispatch_requests SET state='uncertain' WHERE id=? AND state='intent'",(identifier,))
            if isinstance(error,Exception):
                raise ValueError('Runtime stopped; inspect durable local evidence. No retry or fallback.') from None
            raise
        receipt=self.receipt()
        if receipt['outcome']!='passed': raise ValueError('Strict validation rejected; no repair or redelivery')
        return self.public_receipt(receipt),identifier

    def public_receipt(self,receipt):
        # Provider reasoning/response identifiers remain in private local evidence, never the panel.
        public={key:receipt[key] for key in ('version','mode','endpoint','model','payload_hash','context_hash',
            'metadata','outcome','credit_usd','observed_at','raw_response_hash','proof','intent_binding_hash')} | {
            'usage':{key:receipt['usage'][key] for key in ('prompt_tokens','completion_tokens','total_tokens')},
            'answer':receipt['diagnostic'].get('answer'),
            'acceptance':receipt['diagnostic'].get('diagnostic',{}).get('acceptance'),
            'restart_rule':'reuse persisted validated receipt; no new request'}
        return public

    def worker(self):
        return Worker(self.store,None,executor=self.execute,authorizer=self.authorize,review_request=self.request_review,project_id=PROJECT)

    def panel(self,folder):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        receipt=self.receipt()
        view={'runtime':self.public_receipt(receipt) if receipt else {'mode':'DISABLED / AWAITING_APPROVAL','payload_hash':PAYLOAD_HASH},
              'tasks':[dict(r) for r in self.store.db.execute('SELECT id,state,reason FROM tasks WHERE project_id=? ORDER BY id',(PROJECT,))],
              'evidence_legend':{'LIVE_NEBIUS':'actual dedicated runtime adapter; client-observed','RECORDED':'frozen V1–V8 history, no current request','MOCK':'offline manufactured response, no provider request'}}
        (folder/'runtime-review.json').write_text(json.dumps(view,indent=2),encoding='utf-8')
        summary=view['runtime']
        header='<section><h2>'+escape(summary['mode'])+'</h2><p>'+escape(summary.get('proof','Transport disabled; awaiting exact live consent and fresh preflight.'))+'</p>'
        if receipt:
            cost_label='Simulated credit estimate' if summary['mode']=='MOCK' else 'API-derived credit usage'
            header+='<p><strong>Validation: '+escape(summary['outcome'])+'</strong> · Answer: '+escape(str(summary['answer']))+'</p><p>'+cost_label+': $'+escape(summary['credit_usd'])+' · '+str(summary['usage']['prompt_tokens'])+' input / '+str(summary['usage']['completion_tokens'])+' completion tokens</p><p>Source: s1 · fictional source statement · application-owned metadata · local_only</p>'
        header+='</section>'
        states=''.join('<li>'+escape(t['id'].split(':')[-1])+': <strong>'+escape(t['state'])+'</strong>'+(' — '+escape(t['reason']) if t['reason'] else '')+'</li>' for t in view['tasks'])
        legend=''.join('<p><strong>'+escape(k)+'</strong>: '+escape(v)+'</p>' for k,v in view['evidence_legend'].items())
        html='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CreatorAgent runtime receipt</title><style>body{font:18px system-ui;background:#eff4f2;color:#173c3b;margin:0}main{max-width:900px;margin:auto;padding:24px}section,details{background:white;padding:24px;border-radius:16px;margin:16px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}h1{font-size:36px}p,li{line-height:1.5}summary{cursor:pointer}</style><main><h1>CreatorAgent runtime receipt</h1><p>Worker → consent / budget → bounded evidence audit → durable provenance → local review</p>'+header+'<section><h2>Persistent workflow</h2><ul>'+states+'</ul><p>Restart reuses a validated receipt. Uncertain delivery stops; it never triggers another request.</p></section><section><h2>Evidence levels</h2>'+legend+'</section><details><summary>Payload binding and source provenance</summary><pre>'+escape(json.dumps(view,indent=2))+'</pre></details><p>A successful scoped fact audit does not establish general reasoning reliability or a production model capability.</p></main></html>'
        (folder/'runtime-review.html').write_text(html,encoding='utf-8')
        return view

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default=None)
    parser.add_argument('--mock',action='store_true',help='Isolated offline state; never a live approval')
    args=parser.parse_args(); root=Path(args.root or ('data/competition-runtime-mock' if args.mock else 'data/competition-runtime'))
    store=Store(root/'state.sqlite')
    try:
        runtime=Runtime(store,transport=MockTransport(synthetic_response('fact')) if args.mock else None,enabled=args.mock)
        if args.mock:
            batch=store.db.execute('SELECT state FROM dispatch_batches WHERE id=?',(runtime.setup['batch'],)).fetchone()
            if batch[0]=='pending': runtime.dispatch.review(runtime.setup['batch'],'approved','explicit offline mock simulation')
            from .cloud_dispatch import CHECKS
            runtime.dispatch.attest(runtime.setup['batch'],{k:'MOCK-only local fixture; not real preflight' for k in CHECKS},store.clock()+3600)
        runtime.worker().run();runtime.panel(root)
    finally: store.close()
    print('Runtime panel prepared; this command has no enabled live execution path.')

if __name__=='__main__': main()
