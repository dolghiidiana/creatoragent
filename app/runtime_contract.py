"""Executable local design specification, NOT a transport or connected worker.

Only locally manufactured responses are used by the accompanying tests.
Never use this module to regrade historical model runs.
"""
import json
from decimal import Decimal
from hashlib import sha256
from .models import digest
from .validator_v8 import inspect

DESIGN_VERSION='competition-runtime-design-1'
EXPECTED=dict(label='source_fact',answer='twelve',source_refs=['s1'],attribution=None,action='local_only')

def reservation(input_limit=4000, completion_limit=2000, input_rate='0.06', output_rate='0.24'):
    if (type(input_limit) is not int or type(completion_limit) is not int
            or not 0<input_limit<=4000 or not 0<completion_limit<=2000):
        raise ValueError('Token bounds differ from proposed scope')
    if Decimal(input_rate)!=Decimal('0.06') or Decimal(output_rate)!=Decimal('0.24'):
        raise ValueError('Rate snapshot changed; fresh review required')
    return (input_limit*Decimal(input_rate)+completion_limit*Decimal(output_rate))/Decimal(1000000)

def compile_metadata(request):
    source=json.loads(request['messages'][1]['content'])
    if source['requested_operation']!='answer_locally':
        raise ValueError('Proposed scope is local fact extraction only')
    if source['sources']['s1']!='The Lantern Notebook is a fictional collection of twelve short reflections about noticing ordinary places.':
        raise ValueError('Reviewed source changed')
    return dict(source_identity='original-public-synthetic-v1:s1',
        source_sha256=sha256(source['sources']['s1'].encode()).hexdigest(),
        speaker=None,personal_report=False,epistemic_status='statement_in_fictional_source_not_external_verification',
        label='source_fact',source_refs=['s1'],attribution=None,action='local_only',
        permissions={'local_preparation':True,'cloud_disclosure':'requires_exact_new_consent','public_execution':False},
        reviewer='synthetic_fixture_author',origin='application_owned_reviewed_metadata',version=DESIGN_VERSION)

def unique_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('Duplicate field')
        result[key]=value
    return result

def assess_local_response(response, request):
    """New local test response only; no live-evidence or historical regrade flag."""
    metadata=compile_metadata(request)
    if response.get('model')!=request['model']: raise ValueError('Model mismatch')
    usage=response.get('usage',{})
    counts=[usage.get(k) for k in ('prompt_tokens','completion_tokens','total_tokens')]
    if (any(type(n) is not int or n<0 for n in counts) or counts[2]!=counts[0]+counts[1]
            or counts[0]>4000 or counts[1]>request['max_completion_tokens']):
        raise ValueError('Accounting uncertainty')
    choices=response.get('choices',[])
    if not isinstance(choices,list) or len(choices)!=1 or choices[0].get('finish_reason')!='stop':
        raise ValueError('Incomplete provider-shaped test response')
    value=json.loads(choices[0]['message']['content'],object_pairs_hook=unique_pairs)
    if type(value) is not dict or set(value)!=set(EXPECTED): raise ValueError('Unexpected fields')
    # Protected fields are checked as a consistency veto, never copied as authority.
    for key in ('label','source_refs','attribution','action'):
        if value[key]!=metadata[key]: raise ValueError('Protected-field mismatch')
    diagnostic=inspect(response,'fact')
    if diagnostic['outcome']!='passed': raise ValueError('Fact contract rejected')
    return {'mode':'local_contract_test_not_live_evidence','metadata':metadata,
            'raw_response_hash':digest(response),'raw_content':choices[0]['message']['content'],
            'answer':value['answer'],'diagnostic':diagnostic,'claim':'source says twelve; no external verification',
            'nvidia_runtime_proven':False}

# Acceptance-state specification for future integration, not a persisted executor.
TRANSITIONS={
    ('prepared','consent_recorded'):'awaiting_preflight',
    ('awaiting_preflight','fresh_checks_pass'):'ready',
    ('ready','atomic_reservation_committed'):'intent_committed',
    ('intent_committed','live_response_accounted_validated_and_persisted'):'completed',
    ('intent_committed','validation_failure'):'blocked',
    ('intent_committed','transport_or_accounting_uncertain'):'uncertain',
}

def next_state(state,event):
    if (state,event) not in TRANSITIONS: raise ValueError('Transition forbidden; no fallback or automatic retry')
    return TRANSITIONS[(state,event)]
