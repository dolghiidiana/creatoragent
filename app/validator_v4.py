"""Future-run comparison policy; never rewrites responses or historical outcomes."""
import json
import re
from .models import digest
from .nvidia_synthetic import CASES, SOURCES

VERSION='synthetic-validator-v4-surface-comparison'
# Explicit short-text contracts only. Quotes, quantities and longer interpretations stay exact.
SHORT_TEXT_CASES=frozenset({'fact','voice','demand'})

def comparison_text(text):
    """Trim outer whitespace and at most one terminal . ! ?; no other transformations."""
    text=text.strip()
    if text.endswith(('.', '!', '?')) and not text.endswith(('..','!!','??','!?','?!','.!','.?','!.','?.')):
        text=text[:-1]
    return text

def equivalent(actual, expected, *, kind='exact'):
    if type(actual) is not str or type(expected) is not str: return False
    if kind!='short_text': return actual==expected
    # Numeric strings can encode decimal precision/signs; do not surface-normalize them.
    if re.search(r'\d',actual) or re.search(r'\d',expected): return actual==expected
    return comparison_text(actual)==comparison_text(expected)

def inspect(response, case_id):
    case=next(c for c in CASES if c['id']==case_id)
    expected=case['expected']; kind='short_text' if case_id in SHORT_TEXT_CASES else 'exact'
    result={'validator_version':VERSION,'comparison_policy':kind,'raw_response_hash':digest(response),
            'surface_equivalent':False,'outcome':'content_failure'}
    choices=response.get('choices',[])
    if len(choices)!=1 or choices[0].get('finish_reason')!='stop': return result
    try: answer=json.loads(choices[0]['message']['content'])
    except (ValueError,KeyError,TypeError): result['outcome']='format_failure'; return result
    if not isinstance(answer,dict) or set(answer)!=set(expected): result['outcome']='format_failure'; return result
    result['raw_answer']=answer['answer']
    refs=answer['source_refs']
    # V4 does not inherit historical numeric source aliases or normalize any protected field.
    if not isinstance(refs,list) or any(type(r) is not str or r not in SOURCES for r in refs):
        result['outcome']='format_failure'; return result
    if refs!=expected['source_refs'] or any(answer[k]!=expected[k] for k in ('label','attribution','action')): return result
    if not equivalent(answer['answer'],expected['answer'],kind=kind): return result
    result['outcome']='passed'
    result['surface_equivalent']=answer['answer']!=expected['answer']
    return result

def validate(response,case_id):
    return inspect(response,case_id)['outcome']
