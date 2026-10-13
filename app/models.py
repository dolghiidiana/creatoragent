"""Offline workflow contracts; no source extraction or network operations."""
import hashlib
import json
from enum import StrEnum

class TaskState(StrEnum):
    PENDING = 'pending'
    READY = 'ready'
    RUNNING = 'running'
    COMPLETED = 'completed'
    APPROVAL = 'awaiting_approval'
    INFORMATION = 'awaiting_information'
    SCHEDULED = 'scheduled'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'

class ApprovalState(StrEnum):
    PENDING = 'pending'
    APPROVED = 'approved'
    REJECTED = 'rejected'
    EXPIRED = 'expired'
    REVOKED = 'revoked'
    INVALIDATED = 'invalidated'

OPERATIONS = frozenset({'brief', 'announcement', 'checklist', 'submit', 'verify', 'followup', 'evidence_dossier'})
DESTINATION = 'simulated://lantern'

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def binding(project, task):
    scope = dict(project=project['id'], identity=project['identity'],
                operation=task['operation'], destination=task['destination'],
                payload_hash=digest(json.loads(task['payload'])),
                artifact_version=task['artifact_version'], action_id=task['action_id'])
    if task['operation'].startswith('launch_'):
        payload = json.loads(task['payload'])
        scope.update(context_hash=payload['context_hash'], plan_hash=payload['plan_hash'],
                     workflow_version=project['workflow_version'], external_execution=False)
    return scope
