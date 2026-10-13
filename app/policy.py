import json
from .models import OPERATIONS, DESTINATION, binding, canonical

def authorize(store, task):
    project = store.project(task['project_id'])
    if project['paused']:
        return 'paused', 'Project paused'
    if task['operation'].startswith('launch_'):
        from .launch_workflow import authorize as launch_authorize
        disposition, reason = launch_authorize(store, project, task)
        if disposition != 'needs_review_approval':
            return disposition, reason
        return approval_authorize(store, project, task)
    if task['operation'] == 'evidence_dossier' and task['project_id'] != 'p2-evidence':
        return 'blocked', 'Private extension is unavailable in this public fixture'
    if task['operation'] not in OPERATIONS or task['operation'] not in json.loads(project['allowed_operations']):
        return 'blocked', 'Operation is not allowed'
    if task['id'] != task['project_id'] + ':' + task['operation']:
        return 'blocked', 'Operation does not match the versioned workflow task'
    expected = DESTINATION if task['operation'] == 'submit' else 'local'
    if task['destination'] != expected or (task['operation'] == 'submit' and project['allowed_destination'] != expected):
        return 'blocked', 'Destination is not allowed'
    if task['operation'] != 'submit':
        return 'allowed', None
    return approval_authorize(store, project, task)

def approval_authorize(store, project, task):
    approval = store.db.execute('SELECT * FROM approvals WHERE task_id=? ORDER BY rowid DESC LIMIT 1', (task['id'],)).fetchone()
    if not approval:
        return 'awaiting_approval', 'Exact approval required'
    if approval['state'] in ('pending','approved') and approval['expires'] <= store.clock():
        store.db.execute("UPDATE approvals SET state='expired',reason='Approval expired' WHERE id=?", (approval['id'],))
        store.event(task['id'], 'approval_expired', {})
        return 'awaiting_approval', 'Approval expired'
    if approval['binding'] != canonical(binding(project, task)):
        store.db.execute("UPDATE approvals SET state='invalidated',reason='Approval scope changed' WHERE id=? AND state IN ('pending','approved')", (approval['id'],))
        store.event(task['id'], 'approval_invalidated', {})
        return 'awaiting_approval', 'Approval scope changed'
    if approval['state'] == 'rejected':
        return 'cancelled', 'Approval rejected'
    if approval['state'] != 'approved':
        return 'awaiting_approval', 'Approval is ' + approval['state']
    return 'allowed', None
