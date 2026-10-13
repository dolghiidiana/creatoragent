import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from . import approvals, operations, policy
from .adapters.fake_destination import Conflict
from .models import canonical

@contextmanager
def worker_lock(path):
    # OS-owned lock is released even on hard termination. Never reclaim a live worker.
    handle = open(str(path) + '.worker.lock', 'a+b')
    handle.seek(0)
    handle.write(b'0')
    handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Another worker is active')
    try:
        yield
    finally:
        handle.close()

class Worker:
    def __init__(self, store, destination, lease_seconds=30, retry_seconds=1, crash=None, executor=operations.execute, authorizer=policy.authorize, review_request=approvals.request, project_id=None):
        self.store, self.destination = store, destination
        self.owner = uuid.uuid4().hex
        self.lease_seconds, self.retry_seconds = lease_seconds, retry_seconds
        self.crash, self.executor = crash, executor
        self.authorizer, self.review_request = authorizer, review_request
        self.project_id=project_id

    def checkpoint(self, point, task):
        if self.crash == point and task['operation'] == 'submit':
            os._exit(73)

    def recover(self):
        # Called only while holding the OS lock: no old worker can still dispatch.
        with self.store.tx():
            rows = self.store.db.execute("SELECT * FROM tasks WHERE state='running' AND lease_expiry<=? AND (? IS NULL OR project_id=?)", (self.store.clock(),self.project_id,self.project_id)).fetchall()
            for task in rows:
                self.store.db.execute("UPDATE attempts SET ended=?,outcome='interrupted' WHERE task_id=? AND ended IS NULL", (self.store.clock(), task['id']))
                self.store.change_state(task['id'], 'ready', 'Expired interrupted claim; reconcile before dispatch')
                self.store.event(task['id'], 'recovered', {'generation': task['generation']})

            waiting = self.store.db.execute("SELECT * FROM tasks WHERE state='awaiting_approval' AND (? IS NULL OR project_id=?)",(self.project_id,self.project_id)).fetchall()
            for task in waiting:
                disposition, reason = self.authorizer(self.store, task)
                if disposition == 'allowed':
                    self.store.change_state(task['id'], 'ready')
                elif disposition == 'awaiting_approval':
                    self.review_request(self.store, task)
                elif disposition != 'paused':
                    self.store.change_state(task['id'], disposition, reason)

    def step(self):
        task = self.store.claim(self.owner, self.lease_seconds,project_id=self.project_id)
        if not task:
            return False
        self.checkpoint('after_claim', task)
        with self.store.tx():
            disposition, reason = self.authorizer(self.store, self.store.task(task['id']))
            if disposition != 'allowed':
                self.store.change_state(task['id'], 'ready' if disposition == 'paused' else disposition, reason)
                if disposition == 'awaiting_approval':
                    self.review_request(self.store, self.store.task(task['id']))
                return True
            self.store.db.execute("UPDATE tasks SET attempts=attempts+1 WHERE id=?", (task['id'],))
            self.store.db.execute('INSERT INTO attempts(task_id,action_id,generation,started,outcome) VALUES(?,?,?,?,?)',
                                  (task['id'], task['action_id'], task['generation'], self.store.clock(), 'intent'))
        self.checkpoint('before_dispatch', task)
        # The persisted intent is the dispatch boundary. Later revocation cannot undo it.
        try:
            if task['operation'] == 'submit':
                receipt = self.destination.lookup(task['action_id'], json.loads(task['payload']))
                if receipt:
                    result = {'simulated': True, 'receipt': receipt}
                else:
                    result, receipt = self.executor(self.store, task, self.destination)
            else:
                result, receipt = self.executor(self.store, task, self.destination)
            self.checkpoint('after_effect', task)
            self.store.finish(task, result, receipt)
        except (operations.MissingInformation, Conflict, sqlite3.Error, OSError, ValueError, RuntimeError) as error:
            with self.store.tx():
                if not self.store.owns(task):
                    raise RuntimeError('Stale failed claim') from error
                current = self.store.task(task['id'])
                if isinstance(error, operations.MissingInformation):
                    state = 'awaiting_information'
                elif isinstance(error, (Conflict, sqlite3.Error, RuntimeError, ValueError)):
                    state = 'blocked'
                else:
                    state = 'scheduled' if current['attempts'] < current['max_attempts'] else 'blocked'
                    self.store.db.execute('UPDATE tasks SET due=? WHERE id=?', (self.store.clock() + self.retry_seconds * 2 ** (current['attempts'] - 1), task['id']))
                self.store.db.execute('UPDATE attempts SET ended=?,outcome=?,error=? WHERE task_id=? AND generation=?',
                                      (self.store.clock(), state, str(error), task['id'], task['generation']))
                self.store.change_state(task['id'], state, str(error))
        return True

    def run(self, watch=False, poll=.1):
        with worker_lock(self.store.path):
            while True:
                self.recover()
                if self.step():
                    continue
                if not watch:
                    break
                time.sleep(poll)
