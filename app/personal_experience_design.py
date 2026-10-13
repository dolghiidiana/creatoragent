"""Local design prototype only. Not a live validator, historical regrader, or worker."""
import json
from dataclasses import dataclass
from hashlib import sha256
from .models import digest

VERSION = 'local-personal-report-metadata-design-v1'
FIXTURE_TEXT = ('The fictional narrator says: I felt calmer after my ten-minute walk. '
                'This is my experience, not a promise for other people.')
QUOTE = 'I felt calmer after my ten-minute walk.'
LIMIT = 'This is my experience, not a promise for other people.'

@dataclass(frozen=True)
class Annotation:
    source_id: str
    source_sha256: str
    speaker: str
    claim_kind: str
    epistemic_status: str
    quote_span: tuple
    limitation_span: tuple
    reviewed_by: str
    version: str

def span(text):
    start = FIXTURE_TEXT.index(text)
    return (start, start+len(text))

# Author-reviewed synthetic annotation. Not inferred from first-person keywords,
# document author names, model output, or embedded instructions.
ANNOTATION = Annotation('s2',sha256(FIXTURE_TEXT.encode('utf-8')).hexdigest(),
    'fictional narrator','personal_report','subjective_report_not_independently_verified',
    span(QUOTE),span(LIMIT),'synthetic_fixture_author',VERSION)

def compile_context(sources, source_ids=('s2',), operation='check_universal_support'):
    if source_ids != ('s2',) or operation != 'check_universal_support':
        raise ValueError('Unknown or mixed-source/task scope requires local review')
    text = sources.get('s2')
    if not isinstance(text,str) or sha256(text.encode('utf-8')).hexdigest() != ANNOTATION.source_sha256:
        raise ValueError('Source missing or reviewed annotation stale')
    for bounds, expected in ((ANNOTATION.quote_span,QUOTE),(ANNOTATION.limitation_span,LIMIT)):
        if text[bounds[0]:bounds[1]] != expected:
            raise ValueError('Reviewed source span changed')
    return dict(design_version=VERSION,task=operation,source_refs=['s2'],
        attribution=ANNOTATION.speaker,claim_kind=ANNOTATION.claim_kind,
        epistemic_status=ANNOTATION.epistemic_status,
        evidence_spans=dict(report=list(ANNOTATION.quote_span),limitation=list(ANNOTATION.limitation_span)),
        source_sha256=ANNOTATION.source_sha256,annotation_reviewed_by=ANNOTATION.reviewed_by,
        annotation_version=ANNOTATION.version)

def unique_pairs(pairs):
    value = {}
    for key,item in pairs:
        if key in value: raise ValueError('Duplicate key')
        value[key] = item
    return value

def prepare_local_result(raw_decision, sources, context):
    """A new application result, never a corrected model response or old grade."""
    approved = compile_context(sources)
    if context != approved:
        raise ValueError('Application-owned annotation/task context changed')
    decision = json.loads(raw_decision,object_pairs_hook=unique_pairs)
    if not isinstance(decision,dict) or set(decision) != {'assessment','source_refs'}:
        raise ValueError('Only evidence assessment and exact citations accepted')
    if decision != dict(assessment='personal_report_only',source_refs=['s2']):
        raise ValueError('Unsupported assessment or references; no output rendered')
    return dict(design_version=VERSION,
        label='attributed_experience',attribution=approved['attribution'],source_refs=['s2'],action='local_only',
        claim_kind=approved['claim_kind'],epistemic_status=approved['epistemic_status'],
        assessment='personal_report_only',
        answer='The fictional narrator reports feeling calmer after a ten-minute walk. This passage does not establish a guarantee for every reader.',
        origin='local_template',metadata_origin='reviewed_source_annotation_and_task_context',
        raw_decision_sha256=sha256(raw_decision.encode('utf-8')).hexdigest(),
        context_hash=digest(approved),independent_effect_verification=False)
