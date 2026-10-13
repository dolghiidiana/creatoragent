"""Consent-gated preparation only. No credentials or transport are accessed here."""
import json
from decimal import Decimal
from .models import canonical,digest

ENDPOINT = 'https://api.tokenfactory.nebius.com/v1/'
MODEL = 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B'

class NebiusPreparation:
    def __init__(self,store):
        self.store=store
        store.db.executescript('''
        CREATE TABLE IF NOT EXISTS cloud_proposals(
          id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload_hash TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('proposed','approved','revoked')),
          approver TEXT, expires REAL NOT NULL);
        ''')

    def propose(self,request,scope,expires):
        required={'source_refs','purpose','context_hash','endpoint','model_id','max_requests','credit_cap_usd',
                  'out_of_pocket_usd','privacy_confirmed','accounting_confirmed','input_tokens_verified',
                  'completion_includes_reasoning','rate_snapshot'}
        if set(scope)!=required or scope['endpoint']!=ENDPOINT or scope['model_id']!=MODEL:
            raise ValueError('Exact provider/model/scope required')
        if not scope['source_refs'] or not scope['purpose'] or not scope['context_hash'] or expires<=self.store.clock():
            raise ValueError('Exact sources, context, purpose and future expiry required')
        if type(scope['max_requests']) is not int or not 1<=scope['max_requests']<=24:
            raise ValueError('Explicit bounded request count required')
        if Decimal(str(scope['out_of_pocket_usd']))!=0:
            raise ValueError('Out-of-pocket spending forbidden')
        cap=Decimal(str(scope['credit_cap_usd']))
        if not cap.is_finite() or not 0<cap<=Decimal('0.05'):
            raise ValueError('Cumulative credit ceiling invalid')
        if set(request)!={'model','messages','max_completion_tokens'} or request['model']!=MODEL:
            raise ValueError('Request contract changed')
        if type(request['max_completion_tokens']) is not int or not 1<=request['max_completion_tokens']<=4000:
            raise ValueError('Total completion/reasoning limit invalid')
        if not isinstance(request['messages'],list) or not request['messages'] or any(
                set(m)!={'role','content'} or m['role'] not in {'system','user'} or not isinstance(m['content'],str)
                for m in request['messages']):
            raise ValueError('Unsupported messages')
        proposal_id=digest({'scope':scope,'payload_hash':digest(request),'expires':expires})
        with self.store.tx():
            self.store.db.execute('INSERT OR IGNORE INTO cloud_proposals VALUES(?,?,?,?,NULL,?)',
                                 (proposal_id,canonical(scope),digest(request),'proposed',expires))
            self.store.event(None,'cloud_proposal_prepared',{'proposal_id':proposal_id,'transport_enabled':False})
        return proposal_id

    def review(self,proposal_id,decision,approver):
        if decision not in {'approved','revoked'} or not approver:
            raise ValueError('Explicit reviewer and decision required')
        with self.store.tx():
            row=self.store.db.execute('SELECT * FROM cloud_proposals WHERE id=?',(proposal_id,)).fetchone()
            if not row or row['expires']<=self.store.clock() or (decision=='approved' and row['state']!='proposed'):
                raise ValueError('Proposal is missing, expired or no longer pending')
            self.store.db.execute('UPDATE cloud_proposals SET state=?,approver=? WHERE id=?',(decision,approver,proposal_id))
            self.store.event(None,'cloud_proposal_'+decision,{'proposal_id':proposal_id,'approver':approver})

    def readiness(self,proposal_id,request,current_context_hash):
        row=self.store.db.execute('SELECT * FROM cloud_proposals WHERE id=?',(proposal_id,)).fetchone()
        reasons=[]
        if not row:
            return {'ready_for_future_transport':False,'transport_enabled':False,'reasons':['Unknown proposal']}
        scope=json.loads(row['scope'])
        if row['state']!='approved': reasons.append('Exact disclosure/spending approval missing or revoked')
        if row['expires']<=self.store.clock(): reasons.append('Consent expired')
        if digest(request)!=row['payload_hash']: reasons.append('Exact request changed')
        if current_context_hash!=scope['context_hash']: reasons.append('Project context changed')
        for flag in ('privacy_confirmed','accounting_confirmed','input_tokens_verified','completion_includes_reasoning'):
            if scope[flag] is not True: reasons.append(flag+' is unconfirmed')
        if not scope['rate_snapshot']: reasons.append('Current rate snapshot missing')
        reasons.append('Live transport and atomic consent/budget dispatch are not enabled')
        return {'ready_for_future_transport':False,'transport_enabled':False,'reasons':reasons,
                'next_step':'Verify cumulative historical ledger, exact excerpt scope, privacy and rates before wiring dispatch'}
