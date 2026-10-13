"""V6 changes publication citation policy only; no live transport."""
import json
from .models import digest
from .nvidia_synthetic import CASES
from .validator_v5 import inspect as inspect_v5
VERSION='synthetic-validator-v6-publication-support'
PUBLICATION_REFERENCE_FORMS=(('s3',),('s3','s4'))

def inspect(response,case_id):
    if case_id!='boundary':
        d=inspect_v5(response,case_id);d['validator_version']=VERSION;return d
    # Boundary answer remains exact. No response rewriting, normalization or old regrading.
    d={'validator_version':VERSION,'comparison_policy':'exact','raw_response_hash':digest(response),'surface_equivalent':False,'outcome':'content_failure','acceptance':'rejected','source_reference_policy':'s3_required_s4_optional_context'}
    choices=response.get('choices',[])
    if len(choices)!=1 or choices[0].get('finish_reason')!='stop':return d
    try: a=json.loads(choices[0]['message']['content'])
    except (ValueError,KeyError,TypeError):d['outcome']='format_failure';return d
    e=next(c['expected'] for c in CASES if c['id']=='boundary')
    if not isinstance(a,dict):d['outcome']='format_failure';return d
    d['raw_answer']=a.get('answer')
    refs=a.get('source_refs')
    valid_refs=type(refs) is list and all(type(x) is str for x in refs) and tuple(refs) in PUBLICATION_REFERENCE_FORMS
    mismatch=[k for k in ('label','attribution','action') if k not in a or a[k]!=e[k]]
    if not valid_refs:mismatch.append('source_refs')
    if mismatch:d['protected_field_mismatches']=mismatch;return d
    if 'answer' in a and a['answer']!=e['answer']:return d
    if set(a)!=set(e):d['outcome']='format_failure';return d
    d.update(outcome='passed',acceptance='exact_match',accepted_source_form='rule_only' if refs==['s3'] else 'rule_plus_context')
    return d

def validate(response,case_id):return inspect(response,case_id)['outcome']
