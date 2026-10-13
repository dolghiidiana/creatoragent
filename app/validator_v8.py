"""Proposal only: demand decisions are structured; explanations are local templates."""
import json
from .models import digest
from .validator_v6 import inspect as prior_inspect

VERSION = 'synthetic-validator-v8-structured-demand'
EXPECTED = dict(label='insufficient_evidence', answer=None, source_refs=['s3'],
                attribution=None, action='local_only')
EXPLANATIONS = {
    'brief': 'Insufficient evidence for a sales-demand estimate.',
    'detailed': 'Source s3 provides no sales figures or audience survey. The supplied evidence does not support a sales-demand estimate.'}

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result

def inspect(response, case_id):
    if case_id != 'demand':
        result = prior_inspect(response, case_id)
        result['validator_version'] = VERSION
        return result
    result = dict(validator_version=VERSION, raw_response_hash=digest(response),
                  comparison_policy='exact_machine_state_no_model_prose',
                  outcome='content_failure', acceptance='rejected', surface_equivalent=False)
    choices = response.get('choices', [])
    if not isinstance(choices, list) or len(choices) != 1 or choices[0].get('finish_reason') != 'stop':
        return result
    try:
        value = json.loads(choices[0]['message']['content'], object_pairs_hook=unique_object)
    except (ValueError, KeyError, TypeError):
        result['outcome'] = 'format_failure'
        return result
    if not isinstance(value, dict) or set(value) != set(EXPECTED):
        result['outcome'] = 'format_failure'
        return result
    result['raw_answer'] = value['answer']
    mismatch = [key for key in ('label', 'source_refs', 'attribution', 'action')
                if value[key] != EXPECTED[key] or type(value[key]) is not type(EXPECTED[key])]
    if mismatch:
        result['protected_field_mismatches'] = mismatch
        return result
    if value['answer'] is not None:
        result['reason'] = 'Model-authored prose is outside the demand machine contract'
        return result
    result.update(outcome='passed', acceptance='exact_machine_match')
    return result

def render_demand(response, style='detailed'):
    """Only the approved synthetic s3 fixture; not a general manuscript renderer."""
    if inspect(response, 'demand')['outcome'] != 'passed':
        raise ValueError('Invalid machine decision; no explanation rendered')
    return dict(text=EXPLANATIONS[style], origin='local_template', source_refs=['s3'],
                renderer_version=VERSION, raw_response_hash=digest(response))

def validate(response, case_id):
    return inspect(response, case_id)['outcome']
