"""Durable exact-payload consent and conservative accounting. No built-in network transport.

Intent commit is the dispatch linearization point. Revocation prevents later intents,
but cannot recall an already committed request. A lost response never gets retried.
"""
import json
from decimal import Decimal
from .models import canonical, digest
from .evaluation_gate import EvaluationGate
from .nebius_contract import ENDPOINT, MODEL
from .creator_context import CreatorContext

CHECKS = ('model_identity', 'rates', 'zero_retention', 'zero_spend',
          'input_token_count', 'completion_includes_reasoning')

class CloudDispatch:
    def __init__(self, store, *, enabled=False):
        self.store, self.enabled = store, enabled
        self.gate = EvaluationGate(store)
        self.memory = CreatorContext(store)
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS dispatch_batches(
          id TEXT PRIMARY KEY, manifest TEXT NOT NULL, state TEXT NOT NULL,
          reviewer TEXT, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS dispatch_checks(
          batch TEXT PRIMARY KEY, evidence TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS dispatch_requests(
          id TEXT PRIMARY KEY, batch TEXT NOT NULL, slot TEXT NOT NULL,
          state TEXT NOT NULL, payload_hash TEXT NOT NULL, usage TEXT,
          UNIQUE(batch,slot));
        CREATE TABLE IF NOT EXISTS dispatch_history(
          id TEXT PRIMARY KEY, cost TEXT NOT NULL, provenance TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS dispatch_groups(
          id TEXT PRIMARY KEY, cap TEXT NOT NULL, maximum_requests INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS dispatch_group_members(
          batch TEXT PRIMARY KEY, group_id TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS groups_no_update BEFORE UPDATE ON dispatch_groups
          BEGIN SELECT RAISE(ABORT,'stage limits are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS groups_no_delete BEFORE DELETE ON dispatch_groups
          BEGIN SELECT RAISE(ABORT,'stage limits are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS members_no_update BEFORE UPDATE ON dispatch_group_members
          BEGIN SELECT RAISE(ABORT,'stage membership is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS members_no_delete BEFORE DELETE ON dispatch_group_members
          BEGIN SELECT RAISE(ABORT,'stage membership is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS dispatch_history_no_update BEFORE UPDATE ON dispatch_history
          BEGIN SELECT RAISE(ABORT,'historical accounting is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS dispatch_history_no_delete BEFORE DELETE ON dispatch_history
          BEGIN SELECT RAISE(ABORT,'historical accounting is immutable'); END;
        ''')

    def bind_group(self, group_id, batches, cap='0.02', maximum_requests=24):
        amount = Decimal(cap)
        if not group_id or not amount.is_finite() or not 0 < amount <= self.gate.cap or type(maximum_requests) is not int or not 1 <= maximum_requests <= 24:
            raise ValueError('Invalid shared stage limits')
        with self.store.tx():
            old = self.store.db.execute('SELECT * FROM dispatch_groups WHERE id=?',(group_id,)).fetchone()
            if old and (old['cap'] != str(amount) or old['maximum_requests'] != maximum_requests):
                raise ValueError('Stage limits changed')
            self.store.db.execute('INSERT OR IGNORE INTO dispatch_groups VALUES(?,?,?)',(group_id,str(amount),maximum_requests))
            for batch in batches:
                if not self.store.db.execute('SELECT 1 FROM dispatch_batches WHERE id=?',(batch,)).fetchone():
                    raise ValueError('Unknown stage batch')
                member = self.store.db.execute('SELECT group_id FROM dispatch_group_members WHERE batch=?',(batch,)).fetchone()
                if member and member[0] != group_id: raise ValueError('Stage membership changed')
                self.store.db.execute('INSERT OR IGNORE INTO dispatch_group_members VALUES(?,?)',(batch,group_id))
            used, count = self.group_usage(group_id)
            if used > amount or count > maximum_requests: raise ValueError('Existing stage exceeds limits')

    def group_usage(self, group_id):
        batches = [r[0] for r in self.store.db.execute('SELECT batch FROM dispatch_group_members WHERE group_id=?',(group_id,))]
        cost = sum((self._used(b) for b in batches), Decimal(0))
        count = sum(self.store.db.execute('SELECT COUNT(*) FROM dispatch_requests WHERE batch=?',(b,)).fetchone()[0] for b in batches)
        return cost, count

    def import_history(self, record_id, cost, provenance):
        amount = Decimal(str(cost))
        if not amount.is_finite() or amount < 0 or not provenance:
            raise ValueError('Historical cost/provenance required')
        with self.store.tx():
            old = self.store.db.execute('SELECT * FROM dispatch_history WHERE id=?',(record_id,)).fetchone()
            if old:
                if old['cost'] != str(amount) or old['provenance'] != provenance:
                    raise ValueError('History changed')
                return
            if self.store.db.execute('SELECT 1 FROM dispatch_requests').fetchone():
                raise ValueError('Import historical ledger before dispatch')
            if self._used() + amount > self.gate.cap:
                raise ValueError('Historical ceiling exceeded')
            self.store.db.execute('INSERT INTO dispatch_history VALUES(?,?,?)',(record_id,str(amount),provenance))

    def _used(self, batch=None):
        rows = self.store.db.execute('SELECT * FROM evaluation_reservations').fetchall()
        if batch is not None:
            ids = {r[0] for r in self.store.db.execute('SELECT id FROM dispatch_requests WHERE batch=?',(batch,))}
            rows = [r for r in rows if r['id'] in ids]
        total = sum((Decimal(r['actual'] if r['state']=='settled' else r['maximum']) for r in rows),Decimal(0))
        if batch is None:
            total += sum((Decimal(r[0]) for r in self.store.db.execute('SELECT cost FROM dispatch_history')),Decimal(0))
        return total

    def propose(self, manifest, expires):
        required = {'project_id','endpoint','model','context_hash','data_scope','credit_cap','input_rate','output_rate','slots'}
        if set(manifest)!=required or manifest['endpoint']!=ENDPOINT or manifest['model']!=MODEL:
            raise ValueError('Exact model/endpoint manifest required')
        cap = Decimal(str(manifest['credit_cap']))
        if not cap.is_finite() or not 0<cap<=self.gate.cap or expires<=self.store.clock():
            raise ValueError('Invalid budget/expiry')
        if not manifest['context_hash'] or manifest['data_scope']!='original-public-synthetic-v1':
            raise ValueError('Public synthetic scope required')
        if self.memory.current(manifest['project_id'])['hash']!=manifest['context_hash']:
            raise ValueError('Manifest does not match durable creator context')
        rates=[Decimal(str(manifest[k])) for k in ('input_rate','output_rate')]
        if any(not r.is_finite() or r<0 for r in rates): raise ValueError('Invalid rate')
        slots=manifest['slots']
        if not isinstance(slots,list) or not 1<=len(slots)<=24:
            raise ValueError('Invalid request count')
        ids=set()
        for slot in slots:
            if set(slot)!={'id','parent','input_limit','request'} or slot['id'] in ids:
                raise ValueError('Invalid/duplicate slot')
            if slot['parent'] is not None and slot['parent'] not in ids:
                raise ValueError('Repair must follow an approved original slot')
            ids.add(slot['id'])
            req=slot['request']
            if set(req)!={'model','messages','max_completion_tokens','temperature','store','stream','n'}:
                raise ValueError('Request schema changed')
            if req['model']!=MODEL or req['store'] is not False or req['stream'] is not False or req['n']!=1 or req['temperature']!=0:
                raise ValueError('Request parameters changed')
            if any(type(n) is not int or not 0<n<=4000 for n in (slot['input_limit'],req['max_completion_tokens'])):
                raise ValueError('Token ceiling invalid')
            if not req['messages'] or any(set(m)!={'role','content'} or m['role'] not in ('system','user') or not isinstance(m['content'],str) for m in req['messages']):
                raise ValueError('Message scope invalid')
        identifier=digest({'manifest':manifest,'expires':expires})
        with self.store.tx():
            self.store.db.execute('INSERT OR IGNORE INTO dispatch_batches VALUES(?,?,?,NULL,?)',
                                  (identifier,canonical(manifest),'pending',expires))
        return identifier

    def review(self, batch, decision, reviewer):
        if decision not in ('approved','revoked') or not reviewer: raise ValueError('Explicit review required')
        with self.store.tx():
            row=self.store.db.execute('SELECT * FROM dispatch_batches WHERE id=?',(batch,)).fetchone()
            if not row or row['expires']<=self.store.clock() or (decision=='approved' and row['state']!='pending'):
                raise ValueError('Consent unavailable')
            self.store.db.execute('UPDATE dispatch_batches SET state=?,reviewer=? WHERE id=?',(decision,reviewer,batch))

    def attest(self, batch, evidence, expires):
        if set(evidence)!=set(CHECKS) or any(not isinstance(v,str) or not v.strip() for v in evidence.values()):
            raise ValueError('Current evidence for every preflight check required')
        if not self.store.clock()<expires<=self.store.clock()+3600:
            raise ValueError('Preflight expires within one hour')
        with self.store.tx():
            if not self.store.db.execute('SELECT 1 FROM dispatch_batches WHERE id=?',(batch,)).fetchone():
                raise ValueError('Unknown batch')
            self.store.db.execute('INSERT OR REPLACE INTO dispatch_checks VALUES(?,?,?)',(batch,canonical(evidence),expires))

    def reserve_intent(self, batch, slot_id, request, context_hash):
        if not self.enabled: raise ValueError('Live transport disabled')
        with self.store.tx():
            if getattr(self,'intent_guard',None) is not None:
                self.intent_guard()
            row=self.store.db.execute('SELECT * FROM dispatch_batches WHERE id=?',(batch,)).fetchone()
            checks=self.store.db.execute('SELECT * FROM dispatch_checks WHERE batch=?',(batch,)).fetchone()
            if not row or row['state']!='approved' or row['expires']<=self.store.clock():
                raise ValueError('Exact consent missing, revoked or expired')
            if not checks or checks['expires']<=self.store.clock(): raise ValueError('Current preflight missing')
            manifest=json.loads(row['manifest'])
            if digest({'manifest':manifest,'expires':row['expires']})!=batch:
                raise ValueError('Manifest integrity changed')
            if manifest['context_hash']!=context_hash: raise ValueError('Context changed')
            if self.memory.current(manifest['project_id'])['hash']!=context_hash:
                raise ValueError('Durable context changed since consent')
            slot=next((s for s in manifest['slots'] if s['id']==slot_id),None)
            if not slot or canonical(slot['request'])!=canonical(request): raise ValueError('Exact payload changed')
            if self.store.db.execute("SELECT 1 FROM evaluation_reservations WHERE state IN ('reserved','uncertain')").fetchone():
                raise ValueError('Outstanding or uncertain request blocks dispatch')
            if self.store.db.execute("SELECT 1 FROM dispatch_requests WHERE batch=? AND state='content_failure'",(batch,)).fetchone():
                raise ValueError('Content failure stops this evaluation for review')
            if slot['parent']:
                parent=self.store.db.execute('SELECT * FROM dispatch_requests WHERE batch=? AND slot=?',(batch,slot['parent'])).fetchone()
                if not parent or parent['state']!='format_failure': raise ValueError('Repair only for settled formatting failure')
            maximum=(slot['input_limit']*Decimal(str(manifest['input_rate']))+request['max_completion_tokens']*Decimal(str(manifest['output_rate'])))/Decimal(1000000)
            if self._used()+maximum>self.gate.cap or self._used(batch)+maximum>Decimal(str(manifest['credit_cap'])):
                raise ValueError('Credit ceiling exceeded')
            group = self.store.db.execute('SELECT g.* FROM dispatch_groups g JOIN dispatch_group_members m ON m.group_id=g.id WHERE m.batch=?',(batch,)).fetchone()
            if group:
                used, count = self.group_usage(group['id'])
                if used+maximum > Decimal(group['cap']) or count >= group['maximum_requests']:
                    raise ValueError('Shared stage credit/request ceiling exceeded')
            identifier=digest({'batch':batch,'slot':slot_id})
            self.store.db.execute('INSERT INTO dispatch_requests VALUES(?,?,?,?,?,NULL)',(identifier,batch,slot_id,'intent',digest(request)))
            self.store.db.execute('INSERT INTO evaluation_reservations VALUES(?,?,NULL,?)',(identifier,str(maximum),'reserved'))
            self.store.event(None,'cloud_dispatch_intent',{'request_id':identifier,'payload_hash':digest(request),'maximum':str(maximum)})
        return identifier

    def dispatch(self, batch, slot_id, request, context_hash, transport, validator):
        # Injected transport must be separately enabled by the caller. No credential lookup here.
        identifier=self.reserve_intent(batch,slot_id,request,context_hash)
        try:
            response=transport(request)
            self.settle(identifier,response,validator)
        except BaseException:
            with self.store.tx():
                self.store.db.execute("UPDATE evaluation_reservations SET state='uncertain',actual=NULL WHERE id=? AND state='reserved'",(identifier,))
                self.store.db.execute("UPDATE dispatch_requests SET state='uncertain' WHERE id=? AND state='intent'",(identifier,))
            raise
        return identifier

    def settle(self, identifier, response, validator, *, persist_receipt=None):
        with self.store.tx():
            row=self.store.db.execute('SELECT * FROM dispatch_requests WHERE id=?',(identifier,)).fetchone()
            if not row or row['state']!='intent': raise ValueError('Request is not pending')
            batch=self.store.db.execute('SELECT manifest FROM dispatch_batches WHERE id=?',(row['batch'],)).fetchone()
            manifest=json.loads(batch[0]); slot=next(s for s in manifest['slots'] if s['id']==row['slot'])
            usage=response.get('usage',{})
            nums=[usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')]
            if response.get('model')!=manifest['model'] or any(type(n) is not int or n<0 for n in nums) or nums[2]!=nums[0]+nums[1] or nums[0]>slot['input_limit'] or nums[1]>slot['request']['max_completion_tokens']:
                raise ValueError('Model/token accounting uncertain')
            details=usage.get('completion_tokens_details',{})
            if details and (type(details.get('reasoning_tokens')) is not int or not 0<=details['reasoning_tokens']<=nums[1]):
                raise ValueError('Reasoning accounting uncertain')
            outcome=validator(response)
            if outcome not in ('passed','format_failure','content_failure'): raise ValueError('Validator result invalid')
            amount=(nums[0]*Decimal(str(manifest['input_rate']))+nums[1]*Decimal(str(manifest['output_rate'])))/Decimal(1000000)
            self.store.db.execute("UPDATE evaluation_reservations SET state='settled',actual=? WHERE id=?",(str(amount),identifier))
            self.store.db.execute('UPDATE dispatch_requests SET state=?,usage=? WHERE id=?',(outcome,canonical(usage),identifier))
            self.store.event(None,'cloud_usage_settled',{'request_id':identifier,'cost':str(amount),'outcome':outcome})
            # Optional runtime proof is committed in the SAME transaction as accounting.
            # A failed proof write rolls back settlement; dispatch then holds uncertainty.
            if persist_receipt is not None:
                persist_receipt(identifier,response,outcome,str(amount))
