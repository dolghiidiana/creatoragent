"""V5 proposal validator: explicit answer forms; no transport or historical regrading."""
import json
from .models import digest
from .nvidia_synthetic import CASES
from .validator_v4 import inspect as inspect_v4, equivalent, SHORT_TEXT_CASES

VERSION='synthetic-validator-v5-enumerated-evidence-boundary'
DEMAND_FORMS=(
    'insufficient evidence',
    'No sales data is available in the provided sources.',
    'The provided evidence does not support a sales-demand claim.',
    'The provided sources do not establish how many copies will sell.',
)

def inspect(response,case_id):
    # V4 comparison stays in its own file. Never modify response or frozen evidence.
    d=inspect_v4(response,case_id)
    d['validator_version']=VERSION
    d['acceptance']='rejected'
    try: answer=json.loads(response['choices'][0]['message']['content'])
    except (ValueError,KeyError,TypeError,IndexError): return d
    expected=next(c['expected'] for c in CASES if c['id']==case_id)
    if isinstance(answer,dict):
        mismatch=[k for k in ('label','source_refs','attribution','action') if k not in answer or answer[k]!=expected[k]]
        if mismatch:
            d['protected_field_mismatches']=mismatch
            d['outcome']='content_failure'
            return d
    if isinstance(answer,dict) and 'answer' in answer:
        valid_text=(type(answer['answer']) is str and answer['answer'] in DEMAND_FORMS) if case_id=='demand' else equivalent(answer['answer'],expected['answer'],kind='short_text' if case_id in SHORT_TEXT_CASES else 'exact')
        if not valid_text:
            d['outcome']='content_failure'
            return d
    if case_id=='demand':
        d['comparison_policy']='enumerated_semantic_forms_exact_membership'
        d['surface_equivalent']=False
        if len(response.get('choices',[]))==1 and response['choices'][0].get('finish_reason')=='stop' and isinstance(answer,dict) and set(answer)==set(expected):
            d['raw_answer']=answer['answer']
            # Literal membership only: no stemming, substring, case folding, paraphrase engine or LLM judge.
            d['outcome']='passed' if type(answer['answer']) is str and answer['answer'] in DEMAND_FORMS else 'content_failure'
            if d['outcome']=='passed':
                d['acceptance']='exact_match' if answer['answer']==expected['answer'] else 'enumerated_semantic_equivalent'
                d['accepted_form_index']=DEMAND_FORMS.index(answer['answer'])
    elif d['outcome']=='passed':
        d['acceptance']='surface_equivalent' if d['surface_equivalent'] else 'exact_match'
    d['raw_response_hash']=digest(response)
    return d

def validate(response,case_id): return inspect(response,case_id)['outcome']
