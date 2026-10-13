import json

class MissingInformation(ValueError):
    pass

def execute(store, task, destination):
    if task['operation'].startswith('launch_'):
        from .launch_workflow import execute as launch_execute
        return launch_execute(store, task)
    if task['operation'] == 'evidence_dossier':
        from .p2_offline import execute as p2_execute
        return p2_execute(task)
    payload = json.loads(task['payload'])
    if not payload.get('title'):
        raise MissingInformation('Synthetic title is missing; supply title and resume task')
    op = task['operation']
    if op == 'submit':
        receipt = destination.submit(task['action_id'], payload)
        return {'simulated': True, 'receipt': receipt}, receipt
    if op == 'verify':
        source = store.task('lantern:submit')
        receipt = destination.lookup(source['action_id'], json.loads(source['payload']))
        if receipt is None:
            raise RuntimeError('Simulated receipt not found; verification blocked')
        return {'simulated': True, 'verified_receipt': receipt}, receipt
    if op == 'brief':
        return {'title': payload['title'], 'brief': 'A fictional notebook launch exercise.', 'synthetic': True}, None
    if op == 'announcement':
        return {'text': 'Introducing ' + payload['title'], 'synthetic': True}, None
    if op == 'checklist':
        return {'checks': ['fictional identity', 'offline only', 'no real publication']}, None
    if op == 'followup':
        return {'note': 'Prepare the next fictional audience asset.', 'receipt': store.output('lantern:verify'), 'synthetic': True}, None
    raise ValueError('Unknown operation')
