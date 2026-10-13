import json
from .models import binding, canonical, digest

def request(store, task, ttl=86400):
    scope = binding(store.project(task['project_id']), task)
    latest = store.db.execute('SELECT * FROM approvals WHERE task_id=? ORDER BY rowid DESC LIMIT 1', (task['id'],)).fetchone()
    if latest and latest['binding'] == canonical(scope) and latest['state'] in ('pending','approved'):
        return latest['id']
    revision = store.db.execute('SELECT COUNT(*) FROM approvals WHERE task_id=?', (task['id'],)).fetchone()[0] + 1
    request_id = task['id'] + ':approval:' + str(revision)
    content = task['payload']
    if task['operation'] == 'launch_review':
        content = canonical({'scope': 'local demo review only; no public action or disclosure',
                             'package': store.output(task['project_id'] + ':launch_assemble')})
    store.db.execute('INSERT INTO approvals(id,task_id,binding,content,state,expires) VALUES(?,?,?,?,?,?)',
                    (request_id, task['id'], canonical(scope), content, 'pending', store.clock() + ttl))
    store.event(task['id'], 'approval_requested', {'request_id': request_id})
    return request_id

def decide(store, request_id, decision, approver='local creator'):
    if decision not in ('approved','rejected','revoked'):
        raise ValueError('Unsupported decision')
    with store.tx():
        row = store.db.execute('SELECT * FROM approvals WHERE id=?', (request_id,)).fetchone()
        if not row:
            raise ValueError('Unknown approval request')
        if row['state'] == decision:
            return
        permitted = row['state'] == 'pending' and decision in ('approved','rejected') or row['state'] == 'approved' and decision == 'revoked'
        if not permitted:
            raise ValueError('Decision transition is not permitted')
        task = store.task(row['task_id'])
        if task['operation'].startswith('launch_'):
            from .launch_workflow import authorize
            disposition, reason = authorize(store, store.project(task['project_id']), task)
            if disposition not in ('allowed','needs_review_approval'):
                raise ValueError(reason)
        if row['expires'] <= store.clock() or row['binding'] != canonical(binding(store.project(task['project_id']), task)):
            raise ValueError('Request expired or scope changed; worker must create fresh review')
        store.db.execute('UPDATE approvals SET state=?,approver=?,decided=?,reason=? WHERE id=?',
                        (decision, approver, store.clock(), 'Creator decision', request_id))
        store.event(task['id'], 'approval_' + decision, {'request_id': request_id, 'approver': approver})
        if task['state'] not in ('running','completed'):
            store.change_state(task['id'], 'cancelled' if decision == 'rejected' else 'ready' if decision == 'approved' else 'awaiting_approval', 'Creator decision: ' + decision)
        store.refresh()

def packages(store):
    result = []
    for row in store.db.execute('SELECT * FROM approvals ORDER BY rowid'):
        task = store.task(row['task_id'])
        result.append(dict(request_id=row['id'], state=row['state'], expires_utc=row['expires'],
                           scope=json.loads(row['binding']), exact_content=json.loads(row['content']),
                           next_permitted_work=(['prepare local handoff; stop before public execution']
                               if task['operation'].startswith('launch_') else
                               ['verify simulated receipt', 'prepare local follow-up']),
                           simulated=task['operation'] == 'submit', local_only=task['operation'].startswith('launch_')))
    return result
