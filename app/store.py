import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from .models import OPERATIONS, DESTINATION, canonical, digest

class Store:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        for migration in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            self.db.executescript(migration.read_text(encoding='utf-8'))

    def close(self):
        self.db.close()

    @contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def event(self, task, kind, detail):
        self.db.execute('INSERT INTO events(at,task_id,kind,detail) VALUES(?,?,?,?)',
                        (self.clock(), task, kind, canonical(detail)))

    def task(self, task_id):
        return self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()

    def project(self, project_id):
        return self.db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()

    def seed(self):
        fixture = json.loads((Path(__file__).parents[1] / 'tests/fixtures/synthetic-launch.json').read_text())
        with self.tx():
            previous = self.project(fixture['id'])
            if previous:
                if previous['fixture_hash'] != digest(fixture):
                    raise ValueError('Existing workflow fixture differs; do not overwrite durable state')
                return
            self.db.execute('INSERT INTO projects(id,identity,workflow_version,fixture_hash,allowed_operations,allowed_destination) VALUES(?,?,?,?,?,?)',
                            (fixture['id'], fixture['identity'], fixture['workflow_version'], digest(fixture), canonical(sorted({'brief','announcement','checklist','submit','verify','followup'})), DESTINATION))
            graph = {'brief': [], 'announcement': ['brief'], 'checklist': [],
                     'submit': ['announcement'], 'verify': ['submit'], 'followup': ['verify']}
            for op in graph:
                task_id = 'lantern:' + op
                payload = {'title': fixture['title'], 'sentences': fixture['sentences'], 'simulated': True}
                if op == 'submit':
                    payload['announcement_text'] = 'Introducing ' + fixture['title']
                self.db.execute('INSERT INTO tasks(id,project_id,operation,destination,payload,action_id,state) VALUES(?,?,?,?,?,?,?)',
                                (task_id, 'lantern', op, DESTINATION if op == 'submit' else 'local', canonical(payload), task_id + ':v1', 'pending'))
            for op, predecessors in graph.items():
                for pred in predecessors:
                    self.db.execute('INSERT INTO dependencies VALUES(?,?)', ('lantern:' + op, 'lantern:' + pred))
            self.event(None, 'seed', {'fixture': fixture['title'], 'simulated': True})

    def change_state(self, task_id, state, reason=None):
        self.db.execute('UPDATE tasks SET state=?,reason=?,owner=NULL,lease_expiry=NULL WHERE id=?', (state, reason, task_id))
        self.event(task_id, state, {'reason': reason})

    def refresh(self):
        rows = self.db.execute("SELECT * FROM tasks WHERE state IN ('pending','scheduled')").fetchall()
        for task in rows:
            deps = self.db.execute('SELECT t.state,t.id FROM dependencies d JOIN tasks t ON t.id=d.predecessor WHERE d.task_id=?', (task['id'],)).fetchall()
            bad = next((d for d in deps if d['state'] in ('cancelled','blocked')), None)
            if bad:
                self.change_state(task['id'], 'blocked', 'Dependency ' + bad['id'] + ' is ' + bad['state'])
            elif all(d['state'] == 'completed' for d in deps) and task['due'] <= self.clock():
                self.change_state(task['id'], 'ready')

    def claim(self, owner, lease_seconds=30, project_id=None):
        with self.tx():
            self.refresh()
            task = self.db.execute("SELECT t.* FROM tasks t JOIN projects p ON p.id=t.project_id WHERE t.state='ready' AND p.paused=0 AND (? IS NULL OR t.project_id=?) ORDER BY t.id LIMIT 1",(project_id,project_id)).fetchone()
            if not task:
                return None
            self.db.execute("UPDATE tasks SET state='running',owner=?,lease_expiry=?,generation=generation+1 WHERE id=? AND state='ready'",
                            (owner, self.clock() + lease_seconds, task['id']))
            result = self.task(task['id'])
            self.event(task['id'], 'claimed', {'owner': owner, 'generation': result['generation']})
            return result

    def owns(self, task):
        current = self.task(task['id'])
        return current['state'] == 'running' and current['owner'] == task['owner'] and current['generation'] == task['generation']

    def finish(self, task, result, receipt=None):
        with self.tx():
            if not self.owns(task):
                raise RuntimeError('Stale claim cannot commit')
            if task['operation'].startswith('launch_'):
                from .policy import authorize
                disposition, reason = authorize(self, self.task(task['id']))
                if disposition != 'allowed':
                    raise RuntimeError('Launch permission changed before commit: ' + str(reason))
            content = canonical(result)
            self.db.execute('INSERT INTO artifacts VALUES(?,?,?,?)', (task['id'], task['artifact_version'], content, digest(result)))
            self.db.execute('UPDATE tasks SET output_ref=? WHERE id=?', (task['id'] + ':' + str(task['artifact_version']), task['id']))
            self.db.execute("UPDATE attempts SET ended=?,outcome='completed',receipt=? WHERE task_id=? AND generation=?",
                            (self.clock(), canonical(receipt) if receipt else None, task['id'], task['generation']))
            self.change_state(task['id'], 'completed')
            if task['operation'].startswith('launch_'):
                from .launch_workflow import on_finish
                on_finish(self, task, result)

    def output(self, task_id):
        row = self.db.execute('SELECT content,hash FROM artifacts WHERE task_id=? ORDER BY version DESC LIMIT 1', (task_id,)).fetchone()
        if not row:
            return None
        content = json.loads(row['content'])
        if digest(content) != row['hash']:
            raise ValueError('Artifact integrity check failed')
        return content

    def pause(self, paused):
        with self.tx():
            self.db.execute('UPDATE projects SET paused=? WHERE id=?', (int(paused), 'lantern'))
            self.event(None, 'paused' if paused else 'resumed', {})

    def update_payload(self, task_id, payload):
        with self.tx():
            task = self.task(task_id)
            if task['state'] in ('running','completed'):
                raise ValueError('Cannot edit running or completed task')
            self.db.execute('UPDATE tasks SET payload=?,artifact_version=artifact_version+1 WHERE id=?', (canonical(payload), task_id))
            self.db.execute("UPDATE approvals SET state='invalidated',reason='Content changed' WHERE task_id=? AND state IN ('pending','approved')", (task_id,))
            self.change_state(task_id, 'pending', 'Revised content needs fresh review')
